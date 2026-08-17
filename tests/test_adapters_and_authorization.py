from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import httpx
import pytest


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.closed = False

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, ex=None):
        self.values[key] = value

    async def delete(self, key):
        self.values.pop(key, None)

    async def exists(self, key):
        return int(key in self.values)

    async def ping(self):
        return True

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_redis_cache_operations() -> None:
    from app.storage.cache import RedisCache

    cache = RedisCache()
    fake = FakeRedis()
    cache._client = cast(Any, fake)
    assert await cache.get("missing") is None
    await cache.set("key", "value", ttl=10)
    assert await cache.get("key") == "value"
    assert await cache.exists("key")
    await cache.set_idempotency("idem", {"status": "ok"})
    assert await cache.get_idempotency("idem") == {"status": "ok"}
    await cache.blacklist_token("jti", 30)
    assert await cache.is_token_blacklisted("jti")
    await cache.delete("key")
    assert not await cache.exists("key")
    await cache.close()
    assert fake.closed and cache._client is None


@pytest.mark.asyncio
async def test_redis_socket_timeout_exceeds_batch_blocking_pop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.storage import cache as cache_module
    from app.workers.batch_worker import POLL_TIMEOUT

    captured: dict[str, Any] = {}
    fake = FakeRedis()

    def fake_from_url(url: str, **kwargs: Any) -> FakeRedis:
        captured.update(kwargs)
        return fake

    monkeypatch.setattr(cache_module.settings, "redis_url", "redis://redis:6379/0")
    monkeypatch.setattr(cache_module.redis, "from_url", fake_from_url)

    client = await cache_module.RedisCache().get_client()

    assert client is fake
    assert captured["socket_connect_timeout"] == 5
    assert captured["socket_timeout"] > POLL_TIMEOUT


def test_email_console_resend_and_unknown_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import email

    service = email.EmailService()
    monkeypatch.setattr(service.settings, "email_provider", "console")
    assert service.send_email("a@example.com", "Hello", "<p>Hi</p>") == {"id": "console-mock-id"}
    assert service.send_password_reset_email("a@example.com", "token")
    assert service.send_invite_email("a@example.com", "password")
    assert service.send_provision_email("a@example.com", "password")

    monkeypatch.setattr(service.settings, "email_provider", "resend")
    monkeypatch.setattr(email.resend.Emails, "send", lambda params: {"id": "sent"})
    assert service.send_email("a@example.com", "Hello", "Hi") == {"id": "sent"}

    def fail(params):
        raise RuntimeError("provider down")

    monkeypatch.setattr(email.resend.Emails, "send", fail)
    assert service.send_email("a@example.com", "Hello", "Hi") is None
    monkeypatch.setattr(service.settings, "email_provider", "unknown")
    assert service.send_email("a@example.com", "Hello", "Hi") is None


class FakePipeline:
    def __init__(self, results: list[int]) -> None:
        self.results = results

    def __getattr__(self, name):
        return lambda *args, **kwargs: self

    async def execute(self):
        return self.results


class RateRedis:
    def __init__(self, results: list[int]) -> None:
        self.pipe = FakePipeline(results)
        self.removed: list[tuple[str, str]] = []

    def pipeline(self, transaction=True):
        return self.pipe

    async def zrem(self, key, value):
        self.removed.append((key, value))


@pytest.mark.asyncio
async def test_rate_limiter_success_and_both_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.exceptions import RateLimitError
    from app.core.rate_limiter import RateLimiter, settings

    monkeypatch.setattr(settings, "rate_limit_per_minute", 2)
    monkeypatch.setattr(settings, "rate_limit_per_day", 5)
    headers = await RateLimiter(cast(Any, RateRedis([0, 0, 0, 0, 0, 0, 0, 0]))).check_rate_limit(
        "org"
    )
    assert headers["X-RateLimit-Remaining"] == 1

    with pytest.raises(RateLimitError) as minute_error:
        await RateLimiter(cast(Any, RateRedis([0, 2, 0, 0, 0, 0, 0, 0]))).check_rate_limit("org")
    assert minute_error.value.details["retry_after"] == 60

    with pytest.raises(RateLimitError) as day_error:
        await RateLimiter(cast(Any, RateRedis([0, 0, 0, 0, 0, 5, 0, 0]))).check_rate_limit("org")
    assert day_error.value.details["retry_after"] == 3600


@pytest.mark.asyncio
async def test_cross_org_filter_authorization_matrix() -> None:
    from app.core.exceptions import AuthorizationError, ValidationError
    from app.dependencies import AuthContext
    from app.services.cross_org_access import resolve_org_filter

    class DB:
        def __init__(self):
            self.added = []

        def add(self, value):
            self.added.append(value)

    db = DB()
    own_org = uuid4()
    org_auth = AuthContext(
        user_type="org_user",
        organization_id=own_org,
        user_id=uuid4(),
        user_email="org@example.com",
    )
    org_auth.permissions = {"score_requests.list"}
    result = await resolve_org_filter(
        cast(Any, db),
        org_auth,
        requested_org_id=None,
        admin_permission="admin.read",
        resource_type="score",
    )
    assert result.organization_id == own_org and not result.cross_org
    with pytest.raises(AuthorizationError):
        await resolve_org_filter(
            cast(Any, db),
            org_auth,
            requested_org_id=uuid4(),
            admin_permission="admin.read",
            resource_type="score",
        )
    with pytest.raises(ValidationError):
        await resolve_org_filter(
            cast(Any, db),
            org_auth,
            requested_org_id="bad",
            admin_permission="admin.read",
            resource_type="x",
        )

    admin = AuthContext(
        user_type="admin",
        organization_id=None,
        user_id=uuid4(),
        user_email="admin@example.com",
    )
    admin.permissions = {"admin.read"}
    result = await resolve_org_filter(
        cast(Any, db),
        admin,
        requested_org_id=None,
        admin_permission="admin.read",
        resource_type="score",
    )
    assert result.cross_org and db.added
    scoped = await resolve_org_filter(
        cast(Any, db),
        admin,
        requested_org_id=own_org,
        admin_permission="admin.read",
        resource_type="score",
    )
    assert scoped.organization_id == own_org
    admin.permissions = set()
    with pytest.raises(AuthorizationError):
        await resolve_org_filter(
            cast(Any, db),
            admin,
            requested_org_id=None,
            admin_permission="admin.read",
            resource_type="score",
        )


@pytest.mark.asyncio
async def test_orchestrator_proxy_status_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import orchestrator_proxy

    class Client:
        response = httpx.Response(200, json={"decision": "pass"})
        exception: Exception | None = None

        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            if self.exception:
                raise self.exception
            return self.response

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    assert await orchestrator_proxy.forward_rule_evaluation({"x": 1}) == (
        200,
        {"decision": "pass"},
    )
    Client.response = httpx.Response(422, json={"detail": "bad"})
    assert (await orchestrator_proxy.forward_rule_evaluation({}))[0] == 422
    Client.response = httpx.Response(500, text="down")
    assert (await orchestrator_proxy.forward_rule_evaluation({}))[0] == 502
    for error in (httpx.TimeoutException("timeout"), httpx.ConnectError("connect")):
        Client.exception = error
        assert (await orchestrator_proxy.forward_rule_evaluation({}))[0] == 502
