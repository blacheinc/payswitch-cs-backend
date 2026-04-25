"""Platform-admin scoring observability endpoints.

The org-side ``GET /v1/score-requests/stats`` answers "how is *my* org
doing".  Platform admins running the overview tile want "how is the
platform doing, broken down by org" — a different shape and a different
permission gate.

This router exposes that as ``GET /admin/score-requests/stats``:

* No ``organization_id`` query param → platform-wide aggregates plus a
  ``by_org`` breakdown.
* ``organization_id=…`` → same response shape as the org endpoint
  (``by_org`` is an empty list) so the FE can reuse the components.

Mutations (override, outcome, performance) and reads of individual
requests live on the existing org-side router; admins reach them via
the ``admin.score_requests.read`` permission added to the cross-org
flow there.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.db.base import utcnow
from app.dependencies import AuthContext, require_permission
from app.models.organization import Organization
from app.models.score_request import ScoreRequest
from app.models.scoring_result import ScoringDecision, ScoringResult
from app.schemas.score_request import (
    OrgBreakdownEntry,
    PlatformScoreRequestStatsResponse,
)
from app.routers.score_requests import _compute_score_request_stats, _period_bounds
from app.services.cross_org_access import resolve_org_filter

router = APIRouter(prefix="/admin/score-requests", tags=["Platform Admin – Scoring"])


_APPROVED_DECISIONS = (ScoringDecision.APPROVE, ScoringDecision.CONDITIONAL_APPROVE)


@router.get("/stats", response_model=PlatformScoreRequestStatsResponse)
async def get_platform_score_request_stats(
    auth: Annotated[
        AuthContext, Depends(require_permission("admin.score_requests.read"))
    ],
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
) -> PlatformScoreRequestStatsResponse:
    """Platform-wide scoring stats with optional per-org scoping.

    When ``organization_id`` is provided, the response mirrors the org
    endpoint exactly (with ``by_org=[]``). When omitted, ``current`` and
    ``previous`` aggregate across every organisation, and ``by_org``
    carries one row per org for the current window.
    """
    # Reuse the per-org resolver so the audit-log / permission semantics
    # are identical to the cross-org flow on the org-side router.
    org_filter = await resolve_org_filter(
        db, auth,
        requested_org_id=organization_id,
        admin_permission="admin.score_requests.read",
        resource_type="score_requests",
    )

    base_stats = await _compute_score_request_stats(
        db, org_filter.organization_id, period
    )

    by_org: list[OrgBreakdownEntry] = []
    if org_filter.cross_org:
        by_org = await _per_org_breakdown(db, period)

    return PlatformScoreRequestStatsResponse(
        **base_stats.model_dump(),
        by_org=by_org,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _per_org_breakdown(
    db: AsyncSession, period: str
) -> list[OrgBreakdownEntry]:
    """Return one ``OrgBreakdownEntry`` per organisation for the window.

    Single ``GROUP BY organization_id`` query joined to ``organizations``
    so we get human-readable names back without a second round-trip.
    Approval rate is computed from a side count of ``decided + approved``
    in the same query.
    """
    now = utcnow()
    current_start, current_end, _ = _period_bounds(period, now=now)

    # Single GROUP BY org with FILTER clauses for the conditional counts —
    # Postgres-native and one round-trip.
    rows = (
        await db.execute(
            select(
                ScoreRequest.organization_id.label("org_id"),
                Organization.name,
                Organization.short_name,
                func.count(ScoreRequest.id).label("total"),
                func.count(ScoringResult.id).label("decided"),
                func.count(ScoringResult.id).filter(
                    ScoringResult.decision.in_(_APPROVED_DECISIONS)
                ).label("approved"),
            )
            .join(Organization, Organization.id == ScoreRequest.organization_id)
            .outerjoin(
                ScoringResult, ScoringResult.score_request_id == ScoreRequest.id
            )
            .where(
                ScoreRequest.created_at >= current_start,
                ScoreRequest.created_at < current_end,
            )
            .group_by(
                ScoreRequest.organization_id,
                Organization.name,
                Organization.short_name,
            )
            .order_by(func.count(ScoreRequest.id).desc())
        )
    ).all()

    out: list[OrgBreakdownEntry] = []
    for org_id, name, short_name, total, decided, approved in rows:
        out.append(OrgBreakdownEntry(
            organization_id=org_id,
            name=name,
            short_name=short_name,
            total_requests=total,
            decided=decided,
            approval_rate_pct=(
                round(approved * 100 / decided, 1) if decided else 0.0
            ),
        ))
    return out
