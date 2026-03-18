"""Azure Service Bus event publisher and subscriber with in-memory fallback.

Uses Service Bus TOPICS (pub/sub) in production, not queues.
- Publishing sends to a topic via get_topic_sender()
- Subscribing receives from a topic subscription via get_subscription_receiver()
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Coroutine

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
        except asyncio.TimeoutError:
            return None


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
            # Promote message fields to application properties for SQL filtering/routing
            # We only promote primitive types that Service Bus supports
            app_props = {}
            for k, v in message.items():
                if isinstance(v, (str, int, float, bool)):
                    app_props[k] = v
                elif v is None:
                    continue  # Null properties are generally handled as missing
                else:
                    # Convert UUIDs or other objects to strings if they are simple
                    app_props[k] = str(v)

            sb_message = ServiceBusMessage(
                json.dumps(message), application_properties=app_props
            )
            await sender.send_messages(sb_message)
            logger.info(f"[ServiceBus] Message sent to topic '{topic_name}'")

    async def receive(
        self,
        topic_name: str,
        subscription_name: str | None = None,
        timeout: float = 5.0,
    ) -> str | None:
        """Receive a message from a Service Bus topic subscription."""
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

    async def close(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None


# Type alias for subscriber handlers
MessageHandler = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class EventPublisher:
    """Unified event publisher/subscriber that delegates to Service Bus or in-memory."""

    def __init__(self) -> None:
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
        await self._backend.send(
            settings.azure_servicebus_queue_webhooks, delivery_data
        )

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
        """Start a background task that polls a topic subscription and calls handler."""

        async def _poll_loop():
            logger.info(
                f"[Subscriber] Listening on topic '{topic_name}' "
                f"subscription '{subscription_name}'..."
            )
            while True:
                try:
                    raw = await self._backend.receive(
                        topic_name,
                        subscription_name=subscription_name,
                        timeout=poll_interval,
                    )
                    if raw:
                        message = json.loads(raw)
                        logger.info(
                            f"[Subscriber] Received message on "
                            f"'{topic_name}/{subscription_name}'"
                        )
                        await handler(message)
                except asyncio.CancelledError:
                    logger.info(
                        f"[Subscriber] Stopped listening on "
                        f"'{topic_name}/{subscription_name}'"
                    )
                    break
                except Exception as e:
                    logger.exception(
                        f"[Subscriber] Error processing message on "
                        f"'{topic_name}/{subscription_name}': {e}"
                    )
                    await asyncio.sleep(1)  # Back off on error

        task = asyncio.create_task(_poll_loop())
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
