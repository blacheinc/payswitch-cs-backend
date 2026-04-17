"""Platform API logs — GET /admin/api-logs

Lists all API log entries that were recorded for platform (admin) actions,
i.e. rows where organization_id IS NULL.  Only accessible to admin users
who hold the admin.api_logs.read permission.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import PaginatedResponse
from app.db import get_db
from app.dependencies import AuthContext, require_permission
from app.models.api_log import ApiLog
from app.schemas.api_log import ApiLogResponse

router = APIRouter(prefix="/admin/api-logs", tags=["Admin – Platform Logs"])


@router.get("", response_model=PaginatedResponse)
async def list_platform_api_logs(
    auth: Annotated[AuthContext, Depends(require_permission("admin.api_logs.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    method: str | None = Query(default=None, description="Filter by HTTP method"),
    status_code: int | None = Query(default=None, description="Exact status code"),
    status_class: str | None = Query(
        default=None,
        pattern="^[1-5]xx$",
        description="Status class filter, e.g. 4xx or 5xx",
    ),
    path: str | None = Query(default=None, description="Partial path match"),
    from_date: str | None = Query(default=None, description="ISO date, e.g. 2026-01-01"),
    to_date: str | None = Query(default=None, description="ISO date, e.g. 2026-02-01"),
) -> PaginatedResponse:
    """Paginated list of platform-level API request logs.

    These are requests made by admin users against /admin/* endpoints.
    Each log entry includes an ``actor`` object with the admin user's name and email.
    """
    from datetime import datetime as dt

    from app.services.api_log_enrichment import pick_actor, resolve_actors

    # Platform logs have no organization
    base_filter = ApiLog.organization_id.is_(None)

    query = select(ApiLog).where(base_filter)
    count_query = select(func.count()).select_from(ApiLog).where(base_filter)

    if method:
        query = query.where(ApiLog.method == method.upper())
        count_query = count_query.where(ApiLog.method == method.upper())
    if status_code is not None:
        query = query.where(ApiLog.status_code == status_code)
        count_query = count_query.where(ApiLog.status_code == status_code)
    if status_class:
        base = int(status_class[0]) * 100
        query = query.where(ApiLog.status_code >= base, ApiLog.status_code < base + 100)
        count_query = count_query.where(
            ApiLog.status_code >= base, ApiLog.status_code < base + 100
        )
    if path:
        query = query.where(ApiLog.path.ilike(f"%{path}%"))
        count_query = count_query.where(ApiLog.path.ilike(f"%{path}%"))
    if from_date:
        start = dt.fromisoformat(from_date)
        query = query.where(ApiLog.created_at >= start)
        count_query = count_query.where(ApiLog.created_at >= start)
    if to_date:
        end = dt.fromisoformat(to_date)
        query = query.where(ApiLog.created_at <= end)
        count_query = count_query.where(ApiLog.created_at <= end)

    total = (await db.execute(count_query)).scalar() or 0

    offset = (page - 1) * per_page
    query = query.order_by(ApiLog.created_at.desc()).offset(offset).limit(per_page)
    logs = (await db.execute(query)).scalars().all()

    actors = await resolve_actors(db, logs)

    return PaginatedResponse.create(
        items=[
            ApiLogResponse.model_validate(log).model_copy(
                update={"actor": pick_actor(log, actors)}
            )
            for log in logs
        ],
        total=total,
        page=page,
        per_page=per_page,
    )
