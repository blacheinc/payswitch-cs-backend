"""Redis-based sliding window rate limiter."""

from __future__ import annotations

import time

import redis.asyncio as redis

from app.config import get_settings
from app.core.exceptions import RateLimitError

settings = get_settings()


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
            "X-RateLimit-Remaining": max(
                0, settings.rate_limit_per_minute - minute_count - 1
            ),
            "X-RateLimit-Reset": reset_time,
        }
