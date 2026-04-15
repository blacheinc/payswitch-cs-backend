"""GET /v1/monitoring/infrastructure — API latency, error rates, request volume."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.dependencies import get_current_admin
from app.models.user import AdminUser
from app.services.metrics_aggregator import get_infrastructure_metrics

router = APIRouter()


@router.get("/infrastructure")
async def infrastructure_dashboard(
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    period: str = Query("24h", pattern="^(1h|6h|24h|7d|30d)$"),
    endpoint: str | None = Query(None, description="Filter to specific endpoint"),
):
    """Infrastructure Dashboard — API latency, error rates, request volume.

    Query params:
    - period: 1h, 6h, 24h, 7d, 30d (default: 24h)
    - endpoint: filter to a specific endpoint path
    """
    return await get_infrastructure_metrics(
        db, period=period, endpoint_filter=endpoint
    )
