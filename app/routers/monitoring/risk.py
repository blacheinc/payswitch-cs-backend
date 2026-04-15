"""GET /v1/monitoring/risk — approval rates, score distributions, risk tiers."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.dependencies import get_current_admin
from app.models.user import AdminUser
from app.services.metrics_aggregator import get_risk_metrics

router = APIRouter()


@router.get("/risk")
async def risk_dashboard(
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    period: str = Query("7d", pattern="^(24h|7d|30d|90d)$"),
    segment: str | None = Query(None, pattern="^[A-F]$", description="Filter by score grade"),
):
    """Risk Dashboard — live approval rates, score distributions, delinquency trends.

    Query params:
    - period: 24h, 7d, 30d, 90d (default: 7d)
    - segment: filter by score grade A-F
    """
    return await get_risk_metrics(db, period=period, segment_filter=segment)
