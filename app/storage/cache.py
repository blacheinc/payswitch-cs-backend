"""Redis cache client for rate limiting, idempotency, and session caching."""

from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as redis

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class RedisCache:
    """Async Redis client wrapper."""

    def __init__(self) -> None:
        self._client: redis.Redis | None = None

    async def get_client(self) -> redis.Redis:
        if self._client is None:
            self._client = redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
            )
        return self._client

    # ── Key-Value operations ──────────────────────────────────────────────

    async def get(self, key: str) -> str | None:
        client = await self.get_client()
        return await client.get(key)

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
            return json.loads(raw)
        return None

    async def set_idempotency(
        self, key: str, response: dict[str, Any], ttl: int = 86400
    ) -> None:
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
            return await client.ping()
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
