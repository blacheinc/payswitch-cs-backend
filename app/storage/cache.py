"""Redis cache client for rate limiting, idempotency, and session caching."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable
from typing import Any, cast

import redis.asyncio as redis

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# redis-py 8 changed its default socket read timeout from unlimited to five
# seconds.  Batch workers issue BRPOP with a five-second server timeout, so
# relying on that client default creates a race: the socket can time out just
# before Redis returns the normal empty-queue response.  Keep connection
# establishment strict, but allow blocking commands enough time to finish.
REDIS_CONNECT_TIMEOUT_SECONDS = 5
REDIS_SOCKET_TIMEOUT_SECONDS = 30


class InMemoryRedis:
    """Small async Redis-compatible backend for isolated local/E2E runs.

    Enabled only with ``REDIS_URL=memory://``. Production deployments continue
    to require Redis; this backend keeps deterministic handover tests free from
    host-level service dependencies.
    """

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.sorted_sets: dict[str, dict[str, float]] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.values[key] = value

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)

    async def exists(self, key: str) -> int:
        return int(key in self.values)

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        return None

    async def lpush(self, key: str, value: str) -> int:
        values = self.lists.setdefault(key, [])
        values.insert(0, value)
        return len(values)

    async def brpop(self, key: str, timeout: int = 0) -> list[str] | None:
        deadline = time.monotonic() + timeout if timeout else None
        while deadline is None or time.monotonic() < deadline:
            values = self.lists.setdefault(key, [])
            if values:
                return [key, values.pop()]
            await __import__("asyncio").sleep(0.05)
        return None

    def pipeline(self, transaction: bool = True) -> InMemoryPipeline:
        return InMemoryPipeline(self)


class InMemoryPipeline:
    def __init__(self, client: InMemoryRedis) -> None:
        self.client = client
        self.commands: list[tuple[str, tuple[Any, ...]]] = []

    def lpush(self, key: str, value: str) -> InMemoryPipeline:
        self.commands.append(("lpush", (key, value)))
        return self

    def zremrangebyscore(self, key: str, minimum: float, maximum: float) -> InMemoryPipeline:
        self.commands.append(("zremrangebyscore", (key, minimum, maximum)))
        return self

    def zcard(self, key: str) -> InMemoryPipeline:
        self.commands.append(("zcard", (key,)))
        return self

    def zadd(self, key: str, mapping: dict[str, float]) -> InMemoryPipeline:
        self.commands.append(("zadd", (key, mapping)))
        return self

    def expire(self, key: str, seconds: int) -> InMemoryPipeline:
        self.commands.append(("expire", (key, seconds)))
        return self

    async def execute(self) -> list[int]:
        results: list[int] = []
        for name, args in self.commands:
            if name == "lpush":
                results.append(await self.client.lpush(cast(str, args[0]), cast(str, args[1])))
            elif name == "zremrangebyscore":
                key, minimum, maximum = cast(tuple[str, float, float], args)
                values = self.client.sorted_sets.setdefault(key, {})
                removed = [
                    member for member, score in values.items() if minimum <= score <= maximum
                ]
                for member in removed:
                    del values[member]
                results.append(len(removed))
            elif name == "zcard":
                results.append(len(self.client.sorted_sets.setdefault(cast(str, args[0]), {})))
            elif name == "zadd":
                key, mapping = cast(tuple[str, dict[str, float]], args)
                self.client.sorted_sets.setdefault(key, {}).update(mapping)
                results.append(len(mapping))
            else:  # expiry is irrelevant for the short-lived integration process
                results.append(1)
        self.commands.clear()
        return results


class RedisCache:
    """Async Redis client wrapper."""

    def __init__(self) -> None:
        self._client: redis.Redis | None = None

    async def get_client(self) -> redis.Redis:
        if self._client is None:
            if settings.redis_url == "memory://":
                self._client = cast(redis.Redis, InMemoryRedis())
            else:
                self._client = redis.from_url(
                    settings.redis_url,
                    decode_responses=True,
                    socket_connect_timeout=REDIS_CONNECT_TIMEOUT_SECONDS,
                    socket_timeout=REDIS_SOCKET_TIMEOUT_SECONDS,
                )
        return self._client

    # ── Key-Value operations ──────────────────────────────────────────────

    async def get(self, key: str) -> str | None:
        client = await self.get_client()
        return cast(str | None, await client.get(key))

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        client = await self.get_client()
        await client.set(key, value, ex=ttl)

    async def delete(self, key: str) -> None:
        client = await self.get_client()
        await client.delete(key)

    async def exists(self, key: str) -> bool:
        client = await self.get_client()
        return bool(await client.exists(key))

    # ── Idempotency ──────────────────────────────────────────────────────

    async def get_idempotency(self, key: str) -> dict[str, Any] | None:
        """Get cached response for an idempotency key."""
        raw = await self.get(f"idempotency:{key}")
        if raw:
            return cast(dict[str, Any], json.loads(raw))
        return None

    async def set_idempotency(self, key: str, response: dict[str, Any], ttl: int = 86400) -> None:
        """Cache a response for an idempotency key (24h default TTL)."""
        await self.set(f"idempotency:{key}", json.dumps(response), ttl=ttl)

    # ── Session / Token Blacklist ────────────────────────────────────────

    async def blacklist_token(self, jti: str, ttl: int) -> None:
        """Add a JWT token ID to the blacklist."""
        await self.set(f"blacklist:{jti}", "1", ttl=ttl)

    async def is_token_blacklisted(self, jti: str) -> bool:
        return await self.exists(f"blacklist:{jti}")

    # ── Health Check ─────────────────────────────────────────────────────

    async def ping(self) -> bool:
        try:
            client = await self.get_client()
            return bool(await cast(Awaitable[bool], client.ping()))
        except Exception:
            return False

    async def close(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None


# Singleton
_redis_cache: RedisCache | None = None


def get_redis_cache() -> RedisCache:
    global _redis_cache
    if _redis_cache is None:
        _redis_cache = RedisCache()
    return _redis_cache
