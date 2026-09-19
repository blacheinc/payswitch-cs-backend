"""Redis-based sliding window rate limiter."""

from __future__ import annotations

import logging
import time
import uuid

import redis.asyncio as redis
from fastapi import Request

from app.config import get_settings
from app.core.exceptions import RateLimitError

settings = get_settings()
logger = logging.getLogger(__name__)


class RateLimiter:
    """Sliding window rate limiter using Redis sorted sets."""

    def __init__(self, redis_client: redis.Redis) -> None:
        self._redis = redis_client

    async def check_rate_limit(self, org_id: str) -> dict[str, int]:
        """Check and enforce rate limits for an organization.

        Returns rate limit headers dict on success.
        Raises RateLimitError if any limit is exceeded.
        """
        now = time.time()
        minute_key = f"ratelimit:{org_id}:minute"
        day_key = f"ratelimit:{org_id}:day"

        pipe = self._redis.pipeline(transaction=True)

        # Clean expired entries and count current window
        minute_window_start = now - 60
        day_window_start = now - 86400

        # Minute window
        pipe.zremrangebyscore(minute_key, 0, minute_window_start)
        pipe.zcard(minute_key)
        pipe.zadd(minute_key, {f"{now}:{id(pipe)}": now})
        pipe.expire(minute_key, 120)

        # Day window
        pipe.zremrangebyscore(day_key, 0, day_window_start)
        pipe.zcard(day_key)
        pipe.zadd(day_key, {f"{now}:{id(pipe)}:d": now})
        pipe.expire(day_key, 90000)

        results = await pipe.execute()

        minute_count = results[1]
        day_count = results[5]

        # Check limits
        if minute_count >= settings.rate_limit_per_minute:
            # Remove the entry we just added
            await self._redis.zrem(minute_key, f"{now}:{id(pipe)}")
            raise RateLimitError(retry_after=60)

        if day_count >= settings.rate_limit_per_day:
            await self._redis.zrem(day_key, f"{now}:{id(pipe)}:d")
            raise RateLimitError(retry_after=3600)

        # Return headers for the response
        reset_time = int(now) + 60 - int(now % 60)
        return {
            "X-RateLimit-Limit": settings.rate_limit_per_minute,
            "X-RateLimit-Remaining": max(0, settings.rate_limit_per_minute - minute_count - 1),
            "X-RateLimit-Reset": reset_time,
        }


class AuthRateLimiter:
    """Sliding-window limiter for *unauthenticated* auth endpoints.

    ``RateLimiter`` above is keyed by organization and hangs off
    ``get_auth_context``, so it cannot cover login or password reset — those run
    before any auth context exists. This one is keyed by arbitrary strings
    (client IP, target email) instead.

    Unlike the org limiter this does **not** fail open. If Redis is unavailable
    it falls back to an in-process window so a cache outage degrades the
    protection to per-worker instead of removing it entirely — which matters
    here, since fail-open on login is exactly the brute-force window we are
    trying to close.
    """

    # Fallback state, per process. Bounded by _LOCAL_MAX_KEYS.
    _local: dict[str, list[float]] = {}
    _LOCAL_MAX_KEYS = 10_000

    def __init__(self, redis_client: redis.Redis | None = None) -> None:
        self._redis = redis_client

    async def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        """Record a hit against ``key``; return False once ``limit`` is exceeded.

        Callers decide what a refusal means: login raises 429, password reset
        silently skips delivery so the response stays enumeration-safe.
        """
        if self._redis is not None:
            try:
                return await self._allow_redis(key, limit, window_seconds)
            except Exception as e:  # noqa: BLE001 — degrade, don't 500
                logger.warning(
                    "Auth rate limiter falling back to in-process window (%s: %s)",
                    type(e).__name__,
                    e,
                )
        return self._allow_local(key, limit, window_seconds)

    async def _allow_redis(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.time()
        redis_key = f"authratelimit:{key}"

        pipe = self._redis.pipeline(transaction=True)
        pipe.zremrangebyscore(redis_key, 0, now - window_seconds)
        pipe.zcard(redis_key)
        pipe.zadd(redis_key, {f"{now}:{uuid.uuid4().hex}": now})
        pipe.expire(redis_key, window_seconds * 2)
        results = await pipe.execute()

        # Count is taken before our own zadd, so `limit` attempts are allowed.
        return int(results[1]) < limit

    def _allow_local(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.time()
        hits = [t for t in self._local.get(key, []) if t > now - window_seconds]
        allowed = len(hits) < limit
        hits.append(now)
        self._local[key] = hits

        if len(self._local) > self._LOCAL_MAX_KEYS:
            # Drop keys whose newest hit has aged out; this is a fallback path,
            # so approximate eviction is fine.
            for k in [k for k, v in self._local.items() if not v or v[-1] <= now - window_seconds]:
                self._local.pop(k, None)

        return allowed


def client_ip(request: Request) -> str:
    """Best-effort client IP for rate-limit keying.

    The API sits behind a proxy (Vercel -> Helicarrier), so ``request.client``
    is the proxy. ``X-Forwarded-For`` is attacker-controllable, which is why the
    email-keyed limit — not this one — is the primary defense on these
    endpoints; IP keying is a secondary net for spray-style traffic.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
