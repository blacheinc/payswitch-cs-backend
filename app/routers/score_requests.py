"""Score Request router – /v1 API endpoints for scoring."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.core.pagination import PaginatedResponse
from app.db import get_db
from app.db.base import utcnow
from app.dependencies import AuthContext, check_rate_limit
from app.models.score_request import (
    DecisionType,
    LoanPerformance,
    LoanPerformanceStatus,
    RequestSource,
    ScoreDecision,
    ScoreRequest,
    ScoreRequestStatus,
)
from app.schemas.score_request import (
    CreateScoreRequestInput,
    OutcomeResponse,
    PerformanceResponse,
    RecordOutcomeRequest,
    RecordPerformanceRequest,
    ScoreRequestResponse,
    ScoreRequestSummary,
)
from app.services.mock_scorer import generate_mock_score
from app.storage.cache import get_redis_cache

router = APIRouter(prefix="/v1/score-requests", tags=["Scoring API"])


@router.post(
    "",
    response_model=ScoreRequestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_score_request(
    body: CreateScoreRequestInput,
    auth: Annotated[AuthContext, Depends(check_rate_limit)],
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ScoreRequestResponse:
    """Submit a credit score request.

    Supports both API key and JWT authentication.
    Use the `Idempotency-Key` header to prevent duplicate requests.
    """
    # Check idempotency
    if idempotency_key:
        cache = get_redis_cache()
        cached = await cache.get_idempotency(idempotency_key)
        if cached:
            return ScoreRequestResponse(**cached)

        # Also check DB
        existing = await db.execute(
            select(ScoreRequest).where(ScoreRequest.idempotency_key == idempotency_key)
        )
        if existing.scalar_one_or_none():
            raise ConflictError("Duplicate idempotency key")

    # Create tracking ID
    tracking_id = f"SCR-{uuid.uuid4().hex[:12].upper()}"
    payload_dict = body.model_dump(mode="json")

    # Determine source
    source = (
        RequestSource.API if auth.user_type == "api_key" else RequestSource.WEB_PORTAL
    )

    # Create score request record
    score_req = ScoreRequest(
        tracking_id=tracking_id,
        organization_id=auth.organization_id,
        reference_id=body.reference_id,
        status=ScoreRequestStatus.PROCESSING,
        request_source=source,
        request_payload=payload_dict,
        idempotency_key=idempotency_key,
        api_key_id=auth.api_key.id if auth.api_key else None,
        created_by_user_id=auth.user_id,
        callback_url=body.metadata.callback_url if body.metadata else None,
    )
    db.add(score_req)
    await db.flush()

    # Generate mock score (sync – fast path for MVP)
    score_response = generate_mock_score(
        tracking_id=tracking_id,
        request_payload=payload_dict,
        reference_id=body.reference_id,
    )

    # Update the score request with results
    score_req.status = ScoreRequestStatus.COMPLETED
    score_req.score_result = score_response.model_dump(mode="json")
    score_req.score_value = score_response.score.value if score_response.score else None
    score_req.risk_category = (
        score_response.score.risk_category if score_response.score else None
    )
    score_req.confidence = (
        score_response.score.confidence if score_response.score else None
    )
    score_req.model_version = (
        score_response.model_info.model_version if score_response.model_info else None
    )
    score_req.scored_at = utcnow()
    score_req.processing_time_ms = 50  # Mock: fast

    await db.flush()

    # Cache idempotency response
    if idempotency_key:
        cache = get_redis_cache()
        await cache.set_idempotency(
            idempotency_key, score_response.model_dump(mode="json")
        )

    return score_response


@router.get("/{request_id}", response_model=ScoreRequestResponse)
async def get_score_request(
    request_id: str,
    auth: Annotated[AuthContext, Depends(check_rate_limit)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ScoreRequestResponse:
    """Get the result of a score request by tracking ID."""
    result = await db.execute(
        select(ScoreRequest).where(
            ScoreRequest.tracking_id == request_id,
            ScoreRequest.organization_id == auth.organization_id,
        )
    )
    score_req = result.scalar_one_or_none()
    if not score_req:
        raise NotFoundError("Score request", request_id)

    if score_req.score_result:
        return ScoreRequestResponse(**score_req.score_result)

    # Still processing
    return ScoreRequestResponse(
        request_id=score_req.tracking_id,
        reference_id=score_req.reference_id,
        status=score_req.status.value,
        timestamps={
            "requested_at": score_req.created_at.isoformat(),
        },
    )


@router.get("", response_model=PaginatedResponse[ScoreRequestSummary])
async def list_score_requests(
    auth: Annotated[AuthContext, Depends(check_rate_limit)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    status_filter: str | None = Query(None, alias="status"),
) -> PaginatedResponse[ScoreRequestSummary]:
    """List all score requests for the authenticated organization."""
    query = select(ScoreRequest).where(
        ScoreRequest.organization_id == auth.organization_id
    )
    count_query = select(func.count(ScoreRequest.id)).where(
        ScoreRequest.organization_id == auth.organization_id
    )

    if status_filter:
        query = query.where(ScoreRequest.status == ScoreRequestStatus(status_filter))
        count_query = count_query.where(
            ScoreRequest.status == ScoreRequestStatus(status_filter)
        )

    total = (await db.execute(count_query)).scalar_one()
    result = await db.execute(
        query.order_by(ScoreRequest.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    requests = result.scalars().all()

    items = [
        ScoreRequestSummary(
            request_id=r.tracking_id,
            tracking_id=r.tracking_id,
            reference_id=r.reference_id,
            status=r.status.value,
            score_value=r.score_value,
            risk_category=r.risk_category,
            request_source=r.request_source.value,
            created_at=r.created_at,
            scored_at=r.scored_at,
        )
        for r in requests
    ]

    return PaginatedResponse.create(
        items=items, total=total, page=page, per_page=per_page
    )


@router.post("/{request_id}/outcome", response_model=OutcomeResponse)
async def record_outcome(
    request_id: str,
    body: RecordOutcomeRequest,
    auth: Annotated[AuthContext, Depends(check_rate_limit)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> OutcomeResponse:
    """Record the lending decision for a score request (for model feedback)."""
    result = await db.execute(
        select(ScoreRequest).where(
            ScoreRequest.tracking_id == request_id,
            ScoreRequest.organization_id == auth.organization_id,
        )
    )
    score_req = result.scalar_one_or_none()
    if not score_req:
        raise NotFoundError("Score request", request_id)

    decision = ScoreDecision(
        score_request_id=score_req.id,
        decision=DecisionType(body.decision),
        approved_amount=body.approved_amount,
        approved_tenure=body.approved_tenure_months,
        interest_rate=body.interest_rate,
        decline_reason=body.decline_reason,
        decision_notes=body.decision_notes,
        decided_by_user_id=auth.user_id,
        decided_at=utcnow(),
    )
    db.add(decision)

    return OutcomeResponse(request_id=request_id, decision=body.decision)


@router.post("/{request_id}/performance", response_model=PerformanceResponse)
async def record_performance(
    request_id: str,
    body: RecordPerformanceRequest,
    auth: Annotated[AuthContext, Depends(check_rate_limit)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PerformanceResponse:
    """Report loan performance data (for model training feedback loop)."""
    result = await db.execute(
        select(ScoreRequest).where(
            ScoreRequest.tracking_id == request_id,
            ScoreRequest.organization_id == auth.organization_id,
        )
    )
    score_req = result.scalar_one_or_none()
    if not score_req:
        raise NotFoundError("Score request", request_id)

    perf = LoanPerformance(
        score_request_id=score_req.id,
        status=LoanPerformanceStatus(body.status),
        days_past_due=body.days_past_due,
        outstanding_balance=body.outstanding_balance,
        as_of_date=body.as_of_date,
        reported_at=utcnow(),
    )
    db.add(perf)

    return PerformanceResponse(request_id=request_id)
