"""Platform-admin scoring observability endpoints.

The org-side ``GET /v1/score-requests/stats`` answers "how is *my* org
doing".  Platform admins running the overview tile want either:

* **Platform-wide aggregates** — totals across every organisation in the
  window, for a single dashboard tile.  No ``organization_id`` query
  param.
* **Same shape, scoped to a single org** — drill-down view that mirrors
  the org endpoint exactly.  Pass ``organization_id``.

A previous iteration of this endpoint also returned a ``by_org`` list of
per-tenant aggregates, but that mixed two concerns (stats vs. paginated
org list) and had no pagination — see commit log.  The FE should fan out
to ``GET /admin/organizations`` for the paginated org list and call this
endpoint with ``organization_id=`` per visible row when it needs
per-tenant numbers.

Mutations (override, outcome, performance) and reads of individual
requests live on the existing org-side router; admins reach them via
the ``admin.score_requests.read`` permission added to the cross-org
flow there.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.dependencies import AuthContext, require_permission
from app.routers.score_requests import _compute_score_request_stats
from app.schemas.score_request import ScoreRequestStatsResponse
from app.services.cross_org_access import resolve_org_filter

router = APIRouter(prefix="/admin/score-requests", tags=["Platform Admin – Scoring"])


@router.get("/stats", response_model=ScoreRequestStatsResponse)
async def get_platform_score_request_stats(
    auth: Annotated[AuthContext, Depends(require_permission("admin.score_requests.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    period: str = Query(
        "7d",
        pattern=r"^(today|7d|30d|90d)$",
        description="Window anchored at 'now'. 'today' is from start-of-day UTC.",
    ),
    organization_id: str | None = Query(
        None,
        description="Optional — scope to a single org. Omit for platform-wide.",
    ),
) -> ScoreRequestStatsResponse:
    """Platform-wide scoring stats with optional per-org scoping.

    Response shape is exactly the same as the org-side endpoint
    (``/v1/score-requests/stats``).  When ``organization_id`` is omitted
    the aggregates cover every organisation; when set, the response is
    scoped and indistinguishable from what an org user would see for
    their own org.

    This endpoint does **not** return a per-org list — that would conflate
    aggregate stats with a paginated resource and force unbounded
    responses on platforms with many orgs.  Render per-org tables by:

    1. ``GET /admin/organizations?page=&per_page=`` for the paginated org
       list (already supports sort/filter); then
    2. for each visible row, ``GET /admin/score-requests/stats?organization_id=…``.

    With ~10 orgs per page the fan-out is well inside dashboard latency
    budgets and gives the FE full control over sort/filter/pagination.
    """
    org_filter = await resolve_org_filter(
        db,
        auth,
        requested_org_id=organization_id,
        admin_permission="admin.score_requests.read",
        resource_type="score_requests",
    )

    return await _compute_score_request_stats(db, org_filter.organization_id, period)
