"""GET /v1/monitoring/alerts — unified alert feed across all dashboards."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.dependencies import AuthContext, require_permission
from app.services.metrics_aggregator import get_alerts

router = APIRouter()


@router.get("/alerts")
async def alerts_feed(
    _auth: Annotated[AuthContext, Depends(require_permission("monitoring.alerts"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    status: str | None = Query(
        None,
        pattern="^(firing|resolved|ok)$",
        description="Filter by alert status",
    ),
    severity: str | None = Query(
        None,
        pattern="^(warning|critical)$",
        description="Filter by severity",
    ),
    limit: int = Query(50, ge=1, le=200, description="Max alerts to return"),
):
    """Unified alert feed — global alert banner for the frontend.

    Query params:
    - status: firing, resolved, ok
    - severity: warning, critical
    - limit: max alerts (default: 50, max: 200)
    """
    return await get_alerts(
        db,
        status_filter=status,
        severity_filter=severity,
        limit=limit,
    )
