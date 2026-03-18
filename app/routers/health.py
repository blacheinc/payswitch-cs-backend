"""Health & Readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.config import get_settings
from app.db import engine
from app.schemas.common import HealthResponse, ReadinessResponse
from app.storage.cache import get_redis_cache

router = APIRouter(tags=["Health"])
settings = get_settings()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Liveness probe – always returns ok if the process is running."""
    return HealthResponse(
        status="ok",
        version="0.1.0",
        environment=settings.app_env,
    )


@router.get("/ready", response_model=ReadinessResponse)
async def readiness_check() -> ReadinessResponse:
    """Readiness probe – checks all critical dependencies."""
    checks: dict[str, str] = {}

    # Check PostgreSQL
    try:
        async with engine.connect() as conn:
            await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as e:
        checks["postgres"] = f"error: {str(e)[:100]}"

    # Check Redis
    redis_cache = get_redis_cache()
    if await redis_cache.ping():
        checks["redis"] = "ok"
    else:
        checks["redis"] = "error: unreachable"

    overall_status = "ok" if all(v == "ok" for v in checks.values()) else "degraded"

    return ReadinessResponse(status=overall_status, checks=checks)
