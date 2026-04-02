"""Score Request router – /v1 API endpoints for scoring.

Updated for the async scoring flow:
  POST /v1/score-requests → validate, hard-stops, publish to DE, return PROCESSING
  GET  /v1/score-requests/{id} → return result if available, else PROCESSING
  GET  /v1/score-requests/{id}/scoring-result → full 4-model output
  POST /v1/score-requests/{id}/override → manual override for REFER cases
"""

from __future__ import annotations

import logging
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
from app.models.scoring_result import ScoringResult
from app.schemas.score_request import (
    CreateScoreRequestInput,
    HardStopDeclineResponse,
    OutcomeResponse,
    OverrideDecisionRequest,
    OverrideDecisionResponse,
    PerformanceResponse,
    RecordOutcomeRequest,
    RecordPerformanceRequest,
    ScoreRequestResponse,
    ScoreRequestSummary,
    ScoringResultResponse,
)
from app.services.hard_stops import evaluate_hard_stops
from app.storage.cache import get_redis_cache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/score-requests", tags=["Scoring API"])


@router.post(
    "",
    response_model=ScoreRequestResponse | HardStopDeclineResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_score_request(
    body: CreateScoreRequestInput,
    auth: Annotated[AuthContext, Depends(check_rate_limit)],
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ScoreRequestResponse | HardStopDeclineResponse:
    """Submit a credit score request.

    1. Validates input and checks idempotency
    2. Runs hard-stop pre-screening rules (Section 5.2)
    3. If hard-stop triggers → immediate DECLINE
    4. Otherwise → saves as PROCESSING, publishes to DE, returns async response

    Use the `Idempotency-Key` header to prevent duplicate requests.
    """
    # Check idempotency
    if idempotency_key:
        cache = get_redis_cache()
        cached = await cache.get_idempotency(idempotency_key)
        if cached:
            return ScoreRequestResponse(**cached)

        existing = await db.execute(
            select(ScoreRequest).where(ScoreRequest.idempotency_key == idempotency_key)
        )
        if existing.scalar_one_or_none():
            raise ConflictError("Duplicate idempotency key")

    # Create tracking ID
    tracking_id = f"SCR-{uuid.uuid4().hex[:12].upper()}"
    payload_dict = body.model_dump(mode="json")

    # ── Pre-screening: Hard-stop rules ───────────────────────────────────
    applicant_age = None
    if body.applicant.date_of_birth:
        from datetime import date

        today = date.today()
        dob = body.applicant.date_of_birth
        applicant_age = (
            today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        )

    # Extract bureau data for hard-stop evaluation (if provided)
    bureau_data = {}
    if body.bureau_data:
        bureau_data = {
            "total_judgements": body.bureau_data.public_records,
            "total_accounts_in_arrear": body.bureau_data.delinquent_accounts,
        }

    hard_stop_result = evaluate_hard_stops(
        applicant_age=applicant_age,
        bureau_data=bureau_data,
    )

    if not hard_stop_result.passed:
        # Save the declined request for audit
        score_req = ScoreRequest(
            tracking_id=tracking_id,
            organization_id=auth.organization_id,
            reference_id=body.reference_id,
            status=ScoreRequestStatus.COMPLETED,
            request_source=(
                RequestSource.API
                if auth.user_type == "api_key"
                else RequestSource.WEB_PORTAL
            ),
            request_payload=payload_dict,
            idempotency_key=idempotency_key,
            api_key_id=auth.api_key.id if auth.api_key else None,
            created_by_user_id=auth.user_id,
            score_result={
                "decision": "DECLINE",
                "hard_stop": True,
                "triggered_rules": hard_stop_result.triggered_rules,
                "decline_reasons": hard_stop_result.decline_reasons,
            },
            scored_at=utcnow(),
        )
        db.add(score_req)
        await db.flush()

        # Also create a ScoringResult so GET /scoring-result works consistently
        from app.models.scoring_result import ScoringDecision, ScoringResult

        scoring_result = ScoringResult(
            request_id=tracking_id,
            score_request_id=score_req.id,
            scoring_timestamp=utcnow(),
            decision=ScoringDecision.DECLINE,
            condition_applied=[],
            credit_risk=None,
            fraud_detection=None,
            loan_amount=None,
            income_verification=None,
            scoring_metadata={
                "hard_stop": True,
                "triggered_rules": hard_stop_result.triggered_rules,
                "decline_reasons": hard_stop_result.decline_reasons,
            },
            errors=None,
        )
        db.add(scoring_result)

        return HardStopDeclineResponse(
            request_id=tracking_id,
            decline_reasons=hard_stop_result.decline_reasons,
            triggered_rules=hard_stop_result.triggered_rules,
        )

    # ── Normal flow: Save as PROCESSING and publish to DE ────────────────
    source = (
        RequestSource.API if auth.user_type == "api_key" else RequestSource.WEB_PORTAL
    )

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

    # Publish to BE → DE topic (stub: score-applicant)
    # DE will compute features, then publish to inference-request for the AI orchestrator
    # TODO: Replace topic name and message schema once DE team confirms
    try:
        import json as _json
        from app.core.events import get_event_publisher
        from app.storage.cache import get_redis_cache

        # Retrieve cached raw XDS reports from bureau-lookup step
        raw_xds_data = {}
        consumer_id = payload_dict.get("bureau_data", {}).get("consumer_id")
        if consumer_id:
            cache = get_redis_cache()
            cached = await cache._redis.get(f"bureau:{consumer_id}")
            if cached:
                raw_xds_data = _json.loads(cached)
                logger.debug(f"[scoring] Retrieved cached XDS data for consumer {consumer_id}")
            else:
                logger.warning(f"[scoring] No cached XDS data for consumer {consumer_id}")

        publisher = get_event_publisher()
        await publisher.publish(
            topic_name="score-applicant",
            message={
                "request_id": tracking_id,
                "timestamp": score_req.created_at.isoformat(),
                "consumer_id": consumer_id,
                "applicant": payload_dict.get("applicant"),
                "loan_request": payload_dict.get("loan_request"),
                "xds_product_45": raw_xds_data.get("product_45"),
                "xds_product_49": raw_xds_data.get("product_49"),
                "metadata": {
                    "organization_id": str(auth.organization_id),
                    "source": score_req.request_source.value,
                },
            },
        )
        logger.info(f"[scoring] Published score-applicant for {tracking_id}")
    except Exception as e:
        logger.error(f"[scoring] Failed to publish score-applicant: {e}")
        score_req.status = ScoreRequestStatus.FAILED
        score_req.score_result = {"error": f"Failed to submit for scoring: {str(e)}"}

    response = ScoreRequestResponse(
        request_id=tracking_id,
        reference_id=body.reference_id,
        status=score_req.status.value,
        timestamps={"requested_at": score_req.created_at.isoformat()},
    )

    # Cache idempotency response
    if idempotency_key:
        cache = get_redis_cache()
        await cache.set_idempotency(
            idempotency_key, response.model_dump(mode="json")
        )

    return response


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


@router.get("/{request_id}/scoring-result", response_model=ScoringResultResponse)
async def get_scoring_result(
    request_id: str,
    auth: Annotated[AuthContext, Depends(check_rate_limit)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ScoringResultResponse:
    """Get the full 4-model scoring result for a score request.

    Returns the rich output from the AI pipeline including credit risk,
    fraud detection, loan amount, and income verification model results,
    along with SHAP contributions and reason codes.
    """
    # Verify the request belongs to this organization
    sr_result = await db.execute(
        select(ScoreRequest).where(
            ScoreRequest.tracking_id == request_id,
            ScoreRequest.organization_id == auth.organization_id,
        )
    )
    score_req = sr_result.scalar_one_or_none()
    if not score_req:
        raise NotFoundError("Score request", request_id)

    # Look up the scoring result
    result = await db.execute(
        select(ScoringResult).where(ScoringResult.request_id == request_id)
    )
    scoring_result = result.scalar_one_or_none()
    if not scoring_result:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=404,
            detail=(
                "Scoring result not yet available. "
                f"Current status: {score_req.status.value}"
            ),
        )

    return ScoringResultResponse(
        request_id=scoring_result.request_id,
        decision=scoring_result.decision.value,
        condition_applied=scoring_result.condition_applied,
        scoring_timestamp=scoring_result.scoring_timestamp,
        credit_risk=scoring_result.credit_risk,
        fraud_detection=scoring_result.fraud_detection,
        loan_amount=scoring_result.loan_amount,
        income_verification=scoring_result.income_verification,
        scoring_metadata=scoring_result.scoring_metadata,
        errors=scoring_result.errors,
    )


@router.post(
    "/{request_id}/override",
    response_model=OverrideDecisionResponse,
)
async def override_decision(
    request_id: str,
    body: OverrideDecisionRequest,
    auth: Annotated[AuthContext, Depends(check_rate_limit)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> OverrideDecisionResponse:
    """Manually override a REFER decision.

    Only available when the AI pipeline returned REFER. Allows a loan officer
    to make a final decision (APPROVE, CONDITIONAL_APPROVE, or DECLINE).
    """
    # Verify request and get scoring result
    sr_result = await db.execute(
        select(ScoreRequest).where(
            ScoreRequest.tracking_id == request_id,
            ScoreRequest.organization_id == auth.organization_id,
        )
    )
    score_req = sr_result.scalar_one_or_none()
    if not score_req:
        raise NotFoundError("Score request", request_id)

    result = await db.execute(
        select(ScoringResult).where(ScoringResult.request_id == request_id)
    )
    scoring_result = result.scalar_one_or_none()
    if not scoring_result:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=400, detail="Scoring result not available for override"
        )

    from app.models.scoring_result import ScoringDecision

    if scoring_result.decision != ScoringDecision.REFER:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=400,
            detail=f"Can only override REFER decisions (current: {scoring_result.decision.value})",
        )

    # Validate the new decision
    valid_overrides = {"APPROVE", "CONDITIONAL_APPROVE", "DECLINE"}
    if body.decision not in valid_overrides:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=400,
            detail=f"Override decision must be one of: {valid_overrides}",
        )

    previous_decision = scoring_result.decision.value

    # Record the override as a ScoreDecision (for audit trail)
    override_decision_entry = ScoreDecision(
        score_request_id=score_req.id,
        decision=DecisionType(body.decision),
        approved_amount=body.approved_amount,
        approved_tenure=body.approved_tenure_months,
        decision_notes=(
            f"Manual override from {previous_decision}: {body.override_reason}"
        ),
        decided_by_user_id=auth.user_id,
        decided_at=utcnow(),
    )
    db.add(override_decision_entry)

    # Update the scoring result
    scoring_result.decision = ScoringDecision(body.decision)
    scoring_result.condition_applied = body.conditions or scoring_result.condition_applied

    user_email = auth.user_email or str(auth.user_id)

    return OverrideDecisionResponse(
        request_id=request_id,
        previous_decision=previous_decision,
        new_decision=body.decision,
        overridden_by=user_email,
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
