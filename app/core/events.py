"""Azure Service Bus event publisher and subscriber with in-memory fallback.

Uses Service Bus TOPICS (pub/sub) in production, not queues.
- Publishing sends to a topic via get_topic_sender()
- Subscribing receives from a topic subscription via get_subscription_receiver()
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Coroutine
from typing import Any
from uuid import UUID

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class InMemoryQueue:
    """In-memory async queue for local development (no Service Bus)."""

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue] = {}

    def _get_queue(self, name: str) -> asyncio.Queue:
        if name not in self._queues:
            self._queues[name] = asyncio.Queue()
        return self._queues[name]

    async def send(self, topic_name: str, message: dict[str, Any]) -> None:
        q = self._get_queue(topic_name)
        await q.put(json.dumps(message))
        logger.info(f"[InMemoryQueue] Message sent to '{topic_name}'")

    async def receive(
        self,
        topic_name: str,
        subscription_name: str | None = None,
        timeout: float = 5.0,
    ) -> str | None:
        # In-memory ignores subscription_name — just reads from the topic queue
        q = self._get_queue(topic_name)
        try:
            return await asyncio.wait_for(q.get(), timeout=timeout)
        except TimeoutError:
            return None

    async def consume(
        self,
        topic_name: str,
        subscription_name: str | None,
        handler: MessageHandler,
        *,
        timeout: float = 5.0,
    ) -> None:
        """Long-lived consumer loop — kept symmetric with the SB backend
        so subscribers can use the same call shape regardless of mode.
        """
        while True:
            raw = await self.receive(
                topic_name, subscription_name=subscription_name, timeout=timeout
            )
            if raw is None:
                continue
            try:
                await handler(json.loads(raw))
            except Exception:
                logger.exception("[InMemoryQueue] handler error on '%s'", topic_name)


class ServiceBusPublisher:
    """Azure Service Bus topic publisher and subscription receiver."""

    def __init__(self) -> None:
        self._client = None

    async def _get_client(self):
        if self._client is None:
            from azure.servicebus.aio import ServiceBusClient

            self._client = ServiceBusClient.from_connection_string(
                settings.azure_servicebus_connection_string
            )
        return self._client

    async def send(self, topic_name: str, message: dict[str, Any]) -> None:
        """Send a message to a Service Bus topic."""
        from azure.servicebus import ServiceBusMessage

        client = await self._get_client()
        async with client.get_topic_sender(topic_name) as sender:
            app_props: dict[str | bytes, int | float | bytes | bool | str | UUID] = {}
            for k, v in message.items():
                if isinstance(v, (str, int, float, bool)):
                    app_props[k] = v
                elif v is None:
                    continue
                else:
                    app_props[k] = str(v)

            sb_message = ServiceBusMessage(json.dumps(message), application_properties=app_props)
            await sender.send_messages(sb_message)
            logger.info(f"[ServiceBus] Message sent to topic '{topic_name}'")

    async def receive(
        self,
        topic_name: str,
        subscription_name: str | None = None,
        timeout: float = 5.0,
    ) -> str | None:
        """One-shot receive — opens, reads one message, closes.

        Kept for ad-hoc callers and tests. Long-running subscribers should
        use :meth:`consume`, which holds the AMQP link open across polls
        and avoids the attach/detach storm the one-shot path causes when
        polled in a loop.
        """
        if not subscription_name:
            raise ValueError("subscription_name is required for Service Bus topics")

        client = await self._get_client()
        async with client.get_subscription_receiver(
            topic_name=topic_name,
            subscription_name=subscription_name,
            max_wait_time=int(timeout),
        ) as receiver:
            messages = await receiver.receive_messages(
                max_message_count=1, max_wait_time=int(timeout)
            )
            if messages:
                msg = messages[0]
                body = str(msg)
                await receiver.complete_message(msg)
                return body
        return None

    async def consume(
        self,
        topic_name: str,
        subscription_name: str | None,
        handler: MessageHandler,
        *,
        timeout: float = 5.0,
    ) -> None:
        """Hold a single receiver open and dispatch messages to ``handler``.

        Replaces the previous "open / read / close per poll" pattern that
        produced an attach/detach AMQP cycle every few seconds.  The
        receiver is created once and ``receive_messages`` is called in a
        loop on the same link, which is the SDK's documented usage.

        ``handler`` is invoked once per message; we ``complete_message``
        on success and ``abandon_message`` on handler error so failed
        messages remain visible for redelivery (and eventually the
        subscription's dead-letter queue) instead of being silently lost.
        """
        if not subscription_name:
            raise ValueError("subscription_name is required for Service Bus topics")

        client = await self._get_client()
        async with client.get_subscription_receiver(
            topic_name=topic_name,
            subscription_name=subscription_name,
            max_wait_time=int(timeout),
        ) as receiver:
            logger.info(
                "[ServiceBus] consumer attached: %s/%s",
                topic_name,
                subscription_name,
            )
            while True:
                try:
                    messages = await receiver.receive_messages(
                        max_message_count=1, max_wait_time=int(timeout)
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "[ServiceBus] receive_messages failed on %s/%s — will retry",
                        topic_name,
                        subscription_name,
                    )
                    await asyncio.sleep(2)
                    continue

                if not messages:
                    # Empty receive — keep the link open and poll again.
                    continue

                msg = messages[0]
                try:
                    payload = json.loads(str(msg))
                except json.JSONDecodeError:
                    logger.exception(
                        "[ServiceBus] non-JSON message on %s/%s — dead-lettering",
                        topic_name,
                        subscription_name,
                    )
                    await receiver.dead_letter_message(msg, reason="non_json_payload")
                    continue

                try:
                    await handler(payload)
                except Exception:
                    logger.exception(
                        "[ServiceBus] handler error on %s/%s — abandoning",
                        topic_name,
                        subscription_name,
                    )
                    await receiver.abandon_message(msg)
                    continue

                await receiver.complete_message(msg)

    async def close(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None


# Type alias for subscriber handlers
MessageHandler = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class EventPublisher:
    """Unified event publisher/subscriber that delegates to Service Bus or in-memory."""

    def __init__(self) -> None:
        self._backend: ServiceBusPublisher | InMemoryQueue
        if settings.use_service_bus:
            print("Using Service Bus")
            self._backend = ServiceBusPublisher()
        else:
            print("Using In-Memory Queue")
            self._backend = InMemoryQueue()
            logger.warning("Using in-memory queue (no Service Bus connection string).")
        self._subscriber_tasks: list[asyncio.Task] = []

    async def publish_scoring_job(self, job_data: dict[str, Any]) -> None:
        await self._backend.send(settings.azure_servicebus_queue_scoring, job_data)

    async def publish_webhook_delivery(self, delivery_data: dict[str, Any]) -> None:
        await self._backend.send(settings.azure_servicebus_queue_webhooks, delivery_data)

    async def publish_batch_scoring_job(self, job_data: dict[str, Any]) -> None:
        """Publish a batch scoring job to the batch-score-request topic."""
        await self._backend.send(settings.azure_servicebus_topic_batch_score_request, job_data)

    async def publish(self, topic_name: str, message: dict[str, Any]) -> None:
        """Publish a message to a topic."""
        await self._backend.send(topic_name, message)

    def subscribe(
        self,
        topic_name: str,
        subscription_name: str,
        handler: MessageHandler,
        poll_interval: float = 5.0,
    ) -> None:
        """Start a background subscriber task.

        Delegates to ``backend.consume()``, which holds a single receiver
        open and dispatches messages from the same AMQP link.  The
        previous implementation re-opened the link on every poll, causing
        a continuous attach/detach storm visible in the SDK's INFO logs.
        """

        async def _subscriber_task():
            logger.info(
                "[Subscriber] Listening on '%s/%s'",
                topic_name,
                subscription_name,
            )
            try:
                await self._backend.consume(
                    topic_name=topic_name,
                    subscription_name=subscription_name,
                    handler=handler,
                    timeout=poll_interval,
                )
            except asyncio.CancelledError:
                logger.info(
                    "[Subscriber] Stopped listening on '%s/%s'",
                    topic_name,
                    subscription_name,
                )
                raise
            except Exception:
                logger.exception(
                    "[Subscriber] consumer crashed on '%s/%s' — "
                    "task exiting; restart the app to recover",
                    topic_name,
                    subscription_name,
                )

        task = asyncio.create_task(_subscriber_task())
        self._subscriber_tasks.append(task)

    async def close(self) -> None:
        # Cancel all subscriber tasks
        for task in self._subscriber_tasks:
            task.cancel()
        if self._subscriber_tasks:
            await asyncio.gather(*self._subscriber_tasks, return_exceptions=True)
            self._subscriber_tasks.clear()
        if hasattr(self._backend, "close"):
            await self._backend.close()


# Singleton
_event_publisher: EventPublisher | None = None


def get_event_publisher() -> EventPublisher:
    global _event_publisher
    if _event_publisher is None:
        _event_publisher = EventPublisher()
    return _event_publisher
