from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any, cast

import pyotp
import pytest
from jose import JWTError
from pydantic import ValidationError as PydanticValidationError


def test_application_assembles_all_public_routes() -> None:
    from app.main import app

    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/health" in paths
    assert "/v1/score-requests" in paths
    assert "/v1/score/batch" in paths
    assert app.openapi()["info"]["title"]


def test_security_password_jwt_api_keys_totp_and_webhooks() -> None:
    from app.core.security import (
        create_access_token,
        create_refresh_token,
        decode_token,
        generate_api_key,
        generate_email_verification_token,
        generate_temp_password,
        generate_totp_secret,
        generate_webhook_secret,
        get_totp_uri,
        hash_api_key,
        hash_password,
        sign_webhook_payload,
        verify_password,
        verify_totp,
        verify_webhook_signature,
    )

    password_hash = hash_password("StrongPassword!42")
    assert verify_password("StrongPassword!42", password_hash)
    assert not verify_password("wrong", password_hash)

    access = decode_token(create_access_token("user-1", {"role": "admin"}))
    refresh = decode_token(create_refresh_token("user-1"))
    assert access["sub"] == "user-1" and access["type"] == "access"
    assert access["role"] == "admin"
    assert access["iat"] <= access["exp"]
    assert refresh["type"] == "refresh" and refresh["jti"]
    assert refresh["iat"] <= refresh["exp"]
    with pytest.raises(JWTError):
        decode_token(create_access_token("expired", expires_delta=timedelta(seconds=-1)))

    live, live_prefix, live_hash = generate_api_key("production")
    test, test_prefix, _ = generate_api_key("test")
    assert live_prefix == live[:12] and live_hash == hash_api_key(live)
    assert live != test and test_prefix == test[:12]

    secret = generate_totp_secret()
    assert verify_totp(secret, pyotp.TOTP(secret).now())
    assert not verify_totp(secret, "000000")
    assert "otpauth://totp/" in get_totp_uri(secret, "admin@example.com")

    webhook_secret = generate_webhook_secret()
    signature = sign_webhook_payload("payload", webhook_secret)
    assert verify_webhook_signature(b"payload", signature, webhook_secret)
    assert not verify_webhook_signature("changed", signature, webhook_secret)
    assert len(generate_temp_password()) >= 16
    assert len(generate_email_verification_token()) >= 32


def test_pagination_and_business_exceptions() -> None:
    from app.core.exceptions import (
        AuthenticationError,
        AuthorizationError,
        ConflictError,
        IdempotencyConflictError,
        NotFoundError,
        RateLimitError,
        ValidationError,
    )
    from app.core.pagination import PaginatedResponse, PaginationParams

    params = PaginationParams(page=3, per_page=25)
    assert params.offset == 50
    page = PaginatedResponse.create([1, 2], total=51, page=3, per_page=25)
    assert page.total_pages == 3
    assert PaginatedResponse.create([], 0, 1, 0).total_pages == 0
    with pytest.raises(PydanticValidationError):
        PaginationParams(page=0)

    assert NotFoundError("User", "42").status_code == 404
    assert NotFoundError("User").message == "User not found"
    assert ConflictError().status_code == 409
    assert ValidationError("bad", {"field": "email"}).details["field"] == "email"
    assert AuthenticationError().status_code == 401
    assert AuthorizationError().status_code == 403
    assert RateLimitError(10).details == {"retry_after": 10}
    cached = {"request_id": "SCR-1"}
    assert IdempotencyConflictError(cached).cached_response is cached


@pytest.mark.asyncio
async def test_in_memory_events_and_event_publisher(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import events

    queue = events.InMemoryQueue()
    await queue.send("score", {"id": 1})
    assert await queue.receive("score", timeout=0.01) == '{"id": 1}'
    assert await queue.receive("score", timeout=0.01) is None

    handled: list[dict] = []

    async def handler(payload: dict) -> None:
        handled.append(payload)
        raise asyncio.CancelledError

    await queue.send("consume", {"id": 2})
    with pytest.raises(asyncio.CancelledError):
        await queue.consume("consume", None, handler, timeout=0.01)
    assert handled == [{"id": 2}]

    monkeypatch.setattr(events.settings, "azure_servicebus_connection_string", "")
    publisher = events.EventPublisher()
    await publisher.publish("custom", {"ok": True})
    assert await publisher._backend.receive("custom", timeout=0.01) == '{"ok": true}'
    await publisher.publish_scoring_job({"id": 3})
    await publisher.publish_webhook_delivery({"id": 4})
    await publisher.publish_batch_scoring_job({"id": 5})
    await publisher.close()


@pytest.mark.asyncio
async def test_service_bus_backend_send_receive_and_consume() -> None:
    from app.core.events import ServiceBusPublisher

    class Message:
        def __init__(self, body: str) -> None:
            self.body = body

        def __str__(self) -> str:
            return self.body

    class Sender:
        def __init__(self) -> None:
            self.messages: list[Any] = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def send_messages(self, message) -> None:
            self.messages.append(message)

    class Receiver:
        def __init__(self, batches) -> None:
            self.batches = list(batches)
            self.completed: list[Any] = []
            self.abandoned: list[Any] = []
            self.dead_lettered: list[Any] = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def receive_messages(self, **kwargs):
            if not self.batches:
                raise asyncio.CancelledError
            return self.batches.pop(0)

        async def complete_message(self, message) -> None:
            self.completed.append(message)

        async def abandon_message(self, message) -> None:
            self.abandoned.append(message)

        async def dead_letter_message(self, message, **kwargs) -> None:
            self.dead_lettered.append(message)

    class Client:
        def __init__(self, receiver) -> None:
            self.sender = Sender()
            self.receiver = receiver
            self.closed = False

        def get_topic_sender(self, name):
            return self.sender

        def get_subscription_receiver(self, **kwargs):
            return self.receiver

        async def close(self) -> None:
            self.closed = True

    receiver = Receiver([[Message('{"id": 1}')]])
    client = Client(receiver)
    backend = ServiceBusPublisher()
    backend._client = cast(Any, client)
    await backend.send("topic", {"id": 1, "nested": {"ok": True}, "none": None})
    assert client.sender.messages
    assert await backend.receive("topic", "sub") == '{"id": 1}'
    with pytest.raises(ValueError):
        await backend.receive("topic")

    receiver = Receiver([[Message("not-json")], [Message('{"id": 2}')]])
    client.receiver = receiver
    handled = []

    async def handler(payload: dict) -> None:
        handled.append(payload)

    with pytest.raises(asyncio.CancelledError):
        await backend.consume("topic", "sub", handler, timeout=0.01)
    assert handled == [{"id": 2}]
    assert receiver.dead_lettered and receiver.completed
    with pytest.raises(ValueError):
        await backend.consume("topic", None, handler)
    await backend.close()
    assert client.closed
