"""GET /v1/monitoring/infrastructure — API latency, error rates, request volume."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.dependencies import AuthContext, require_permission
from app.services.metrics_aggregator import get_infrastructure_metrics

router = APIRouter()


@router.get("/infrastructure")
async def infrastructure_dashboard(
    _auth: Annotated[AuthContext, Depends(require_permission("monitoring.infrastructure"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    period: str = Query("24h", pattern="^(1h|6h|24h|7d|30d)$"),
    endpoint: str | None = Query(None, description="Filter to specific endpoint"),
):
    """Infrastructure Dashboard — API latency, error rates, request volume.

    Query params:
    - period: 1h, 6h, 24h, 7d, 30d (default: 24h)
    - endpoint: filter to a specific endpoint path
    """
    return await get_infrastructure_metrics(db, period=period, endpoint_filter=endpoint)
