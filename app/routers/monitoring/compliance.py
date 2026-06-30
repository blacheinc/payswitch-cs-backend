"""GET /v1/monitoring/compliance — fairness metrics, audit coverage, data quality."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.dependencies import AuthContext, require_permission
from app.services.metrics_aggregator import get_compliance_metrics

router = APIRouter()


@router.get("/compliance")
async def compliance_dashboard(
    _auth: Annotated[AuthContext, Depends(require_permission("monitoring.compliance"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    period: str = Query("30d", pattern="^(7d|30d|90d)$"),
):
    """Compliance Dashboard — fairness metrics, audit log volume, data quality.

    Query params:
    - period: 7d, 30d, 90d (default: 30d)
    """
    return await get_compliance_metrics(db, period=period)
