"""Score Request router – /v1 API endpoints for scoring.

Updated for the async scoring flow:
  POST /v1/score-requests → validate, hard-stops, publish to DE, return PROCESSING
  GET  /v1/score-requests/{id} → return result if available, else PROCESSING
  GET  /v1/score-requests/{id}/scoring-result → full 4-model output
  POST /v1/score-requests/{id}/override → manual override for REFER cases
"""

from __future__ import annotations

import copy
import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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


def _build_xds_payload(
    cached_xds: dict[str, Any],
    applicant: dict[str, Any] | None,
    employment: dict[str, Any] | None,
    financial_profile: dict[str, Any] | None,
    bureau_data: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build / patch the XDS-format payload that gets sent to the DE.

    The Data Engineer's feature pipeline reads from a single structure
    matching the real XDS API shape (``personalDetailsSummary``,
    ``creditAccountSummary``, ``creditAgreementSummary[]``, etc.).

    * **HIT scenario** – ``cached_xds`` has real bureau data; form values
      override the specific fields the user is allowed to modify.
    * **NO_RECORD scenario** – ``cached_xds`` is empty; a synthetic
      structure is built from the form data so the DE gets the same shape.

    The DE key mapping (from its field-mapping document):
      45:personalDetailsSummary.birthDate        → applicant_age
      45:personalDetailsSummary.dependants        → num_dependants
      45:personalDetailsSummary.employerDetail    → has_employer_detail
      45:personalDetailsSummary.nationalIDNo      → identity_verified
      45:creditAccountSummary.*                   → summary-level features
      45:creditAgreementSummary[]                 → account-level features
      49:detailedFacilityInfo[]                   → mobile loan features
      49:creditAccountSummary.highestAmountTaken  → mobile_max_loan_ghs
    """
    p45 = copy.deepcopy(cached_xds.get("product_45") or {})
    p49 = copy.deepcopy(cached_xds.get("product_49") or {})

    # ── personalDetailsSummary (applicant + employment form fields) ──────
    personal = p45.get("personalDetailsSummary")
    if not isinstance(personal, dict):
        personal = p45.get("personalDetails", {})
    if isinstance(personal, list) and personal:
        personal = dict(personal[0])
    elif not isinstance(personal, dict):
        personal = {}

    if applicant:
        full_name = applicant.get("full_name", "")
        if full_name:
            parts = full_name.strip().split()
            personal["firstName"] = parts[0]
            personal["surname"] = parts[-1] if len(parts) > 1 else parts[0]
            personal["otherNames"] = " ".join(parts[1:-1]) if len(parts) > 2 else ""

        dob = applicant.get("date_of_birth")
        if dob:
            # Convert ISO yyyy-mm-dd → XDS dd/mm/yyyy
            dob_str = str(dob)
            if len(dob_str) >= 10 and dob_str[4] == "-":
                personal["birthDate"] = f"{dob_str[8:10]}/{dob_str[5:7]}/{dob_str[:4]}"
            else:
                personal["birthDate"] = dob_str

        if applicant.get("national_id_number"):
            personal["nationalIDNo"] = applicant["national_id_number"]
        if applicant.get("gender"):
            personal["gender"] = applicant["gender"].capitalize()
        if applicant.get("marital_status"):
            personal["maritalStatus"] = applicant["marital_status"]
        if applicant.get("phone"):
            personal["cellularNo"] = applicant["phone"]
        if applicant.get("email"):
            personal["emailAddress"] = applicant["email"]
        if applicant.get("dependents") is not None:
            personal["dependants"] = str(applicant["dependents"])

        addr = applicant.get("address") or {}
        if addr.get("street"):
            personal["residentialAddress1"] = addr["street"]
        if addr.get("city"):
            personal["residentialAddress2"] = addr["city"]
        if addr.get("region"):
            personal["residentialAddress3"] = addr["region"]

    if employment:
        if employment.get("employer_name"):
            personal["employerDetail"] = employment["employer_name"]

    p45["personalDetailsSummary"] = personal

    # ── creditAccountSummary (from bureau_data.credit_summary) ──────────
    # Maps BureauCreditSummary fields → XDS creditAccountSummary fields.
    summary = p45.get("creditAccountSummary", p45.get("creditAccountSummaryGHS"))
    if isinstance(summary, list) and summary:
        summary = dict(summary[0])
    elif not isinstance(summary, dict):
        summary = {}

    credit_summary = (bureau_data.get("credit_summary") or {}) if bureau_data else {}
    if isinstance(credit_summary, dict):
        _cs_map = {
            "total_accounts": "totalNumberofAccountsGHS",
            "total_monthly_instalment_ghs": "totalMonthlyInstalmentGHS",
            "total_outstanding_debt_ghs": "totalOutstandingdebtGHS",
            "total_accounts_in_arrear": "totalAccountInArrearGHS",
            "total_arrear_amount_ghs": "totalAmountInArrearGHS",
            "total_accounts_good_standing": "totalaccountinGoodconditionGHS",
            "total_judgements": "totalNumberofJudgementGHS",
            "total_judgement_amount_ghs": "totalJudgementAmountGHS",
            "total_dishonoured_cheques": "totalNumberofDishonouredGHS",
        }
        for form_key, xds_key in _cs_map.items():
            val = credit_summary.get(form_key)
            if val is not None:
                summary[xds_key] = str(val)

        # Delinquency rating lives in its own top-level object
        del_rating = credit_summary.get("delinquency_rating")
        if del_rating is not None:
            p45["highestDelinquencyRating"] = {
                "highestDelinquencyRating": str(del_rating)
            }

    # Ensure required keys exist (DE expects these even if "0")
    summary.setdefault("totalNumberofAccountsGHS", "0")
    summary.setdefault("totalActiveAccountsGHS", "0")
    summary.setdefault("totalAccountInArrearGHS", "0")
    summary.setdefault("totalAmountInArrearGHS", "0.00")
    summary.setdefault("totalOutstandingdebtGHS", "0.00")
    summary.setdefault("totalMonthlyInstalmentGHS", "0.00")
    summary.setdefault("totalaccountinGoodconditionGHS", "0")
    summary.setdefault("totalNumberofJudgementGHS", "0")
    summary.setdefault("totalNumberofDishonouredGHS", "0")
    p45["creditAccountSummary"] = summary

    # ── creditAgreementSummary (from bureau_data.credit_accounts) ────────
    # Maps BureauCreditAccount fields → XDS creditAgreementSummary entries.
    # User modifications to individual accounts override cached XDS data.
    credit_accounts = (bureau_data.get("credit_accounts") or []) if bureau_data else []
    if credit_accounts:
        accts = []
        for acct in credit_accounts:
            if not isinstance(acct, dict):
                continue
            accts.append({
                "subscriberName": acct.get("subscriber_name", ""),
                "accountNo": acct.get("account_no", ""),
                "dateAccountOpened": acct.get("date_opened", ""),
                "indicatorDescription": acct.get("facility_type", ""),
                "currency": acct.get("currency", "GHS"),
                "openingBalanceAmt": str(acct.get("credit_limit") or "0"),
                "currentBalanceAmt": str(acct.get("current_balance") or "0"),
                "instalmentAmount": str(acct.get("instalment_amount") or "0"),
                "amountOverdue": str(acct.get("arrear_amount") or "0"),
                "accountStatusCode": acct.get("status_code", ""),
                "monthsInArrears": str(acct.get("months_in_arrears") or "0"),
            })
        p45["creditAgreementSummary"] = accts
    elif not p45.get("creditAgreementSummary") and financial_profile:
        # Fallback: build from financial_profile.existing_loans (NO_RECORD)
        existing = financial_profile.get("existing_loans") or []
        accts = []
        for loan in existing:
            if not isinstance(loan, dict):
                continue
            status = "A"
            if loan.get("status") == "paid_off":
                status = "C"
            accts.append({
                "subscriberName": loan.get("lender_name", ""),
                "accountNo": "",
                "indicatorDescription": loan.get("loan_type", "Personal cash loan"),
                "openingBalanceAmt": str(loan.get("original_amount", "0")),
                "currency": "GHS",
                "currentBalanceAmt": str(loan.get("outstanding_balance", "0")),
                "instalmentAmount": str(loan.get("monthly_payment", "0")),
                "amountOverdue": "0.00",
                "accountStatusCode": status,
                "monthsInArrears": str((loan.get("days_past_due") or 0) // 30),
            })
        p45["creditAgreementSummary"] = accts

    # ── Ensure remaining P45 structures exist ───────────────────────────
    p45.setdefault("highestDelinquencyRating", {"highestDelinquencyRating": "0"})
    p45.setdefault("creditAgreementSummary", [])
    p45.setdefault("accountMonthlyPaymentHistory", [])
    p45.setdefault("enquiryHistory", [])
    p45.setdefault("judgementSummary", [])
    p45.setdefault("defaults", [])
    p45.setdefault("adverseDetails", [])
    p45.setdefault("dudCheqEventSummary", [])
    p45.setdefault("addressHistory", [])

    # ── P49 structure defaults ──────────────────────────────────────────
    if p49:
        p49.setdefault("detailedFacilityInfo", [])
        p49.setdefault("creditAccountSummary", {})

    return {
        "product_45": p45,
        "product_49": p49 or None,
    }


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

    # Fetch cached XDS data from the bureau-lookup step so we can evaluate
    # all 7 hard-stop rules. HS-03, HS-05, and HS-06 require fields that
    # only exist in the raw bureau report, not in the lightweight BureauData body.
    import json as _json
    from app.storage.cache import get_redis_cache as _get_redis_cache

    _raw_xds_data: dict = {}
    consumer_id: str | None = (
        body.bureau_data.consumer_id if body.bureau_data else None
    )
    if consumer_id:
        try:
            _cache = _get_redis_cache()
            _cached_bytes = await _cache.get(f"bureau:{consumer_id}")
            if _cached_bytes:
                _raw_xds_data = _json.loads(_cached_bytes)
        except Exception as _e:
            logger.warning(f"[scoring] XDS cache fetch failed: {_e}")

    # Build the bureau_data dict for hard-stop evaluation.
    # Start with whatever the client submitted (credit_summary from the
    # bureau lookup form), then overlay authoritative values from the
    # cached XDS report where available.
    bureau_data: dict = {}
    if body.bureau_data and body.bureau_data.credit_summary:
        _cs = body.bureau_data.credit_summary
        if _cs.total_judgements is not None:
            bureau_data["total_judgements"] = _cs.total_judgements
        if _cs.total_accounts_in_arrear is not None:
            bureau_data["total_accounts_in_arrear"] = _cs.total_accounts_in_arrear
        if _cs.delinquency_rating is not None:
            bureau_data["delinquency_rating"] = _cs.delinquency_rating

    identity_confidence: float | None = None

    if _raw_xds_data:
        _p45 = _raw_xds_data.get("product_45") or {}
        _p49 = _raw_xds_data.get("product_49") or {}

        # ── Delinquency rating (HS-06) ───────────────────────────────────
        # Prefer P45, fall back to P49
        _del_obj = (
            _p45.get("highestDelinquencyRating")
            or _p49.get("highestDelinquencyRating")
            or {}
        )
        if isinstance(_del_obj, list) and _del_obj:
            _del_obj = _del_obj[0]
        if isinstance(_del_obj, dict):
            bureau_data["delinquency_rating"] = _del_obj.get("highestDelinquencyRating")

        # ── Credit summary overrides (HS-04, HS-07) ──────────────────────
        # P45 uses GHS-suffixed fields; P49 uses unsuffixed fields with
        # slightly different names (e.g. totalAccountarrear vs totalAccountInArrearGHS).
        def _to_int(v):
            try:
                return int(str(v).replace(",", "")) if v is not None else None
            except (ValueError, TypeError):
                return None

        _summary = _p45.get("creditAccountSummary", _p45.get("creditAccountSummaryGHS", {}))
        if isinstance(_summary, list) and _summary:
            _summary = _summary[0]
        if isinstance(_summary, dict):
            _jdg = _to_int(_summary.get("totalNumberofJudgementGHS"))
            _arr = _to_int(_summary.get("totalAccountInArrearGHS"))
            if _jdg is not None:
                bureau_data["total_judgements"] = _jdg
            if _arr is not None:
                bureau_data["total_accounts_in_arrear"] = _arr

        # Fall back to P49 summary when P45 didn't provide values
        if "total_accounts_in_arrear" not in bureau_data:
            _p49_summary = _p49.get("creditAccountSummary", {})
            if isinstance(_p49_summary, list) and _p49_summary:
                _p49_summary = _p49_summary[0]
            if isinstance(_p49_summary, dict):
                _p49_arr = _to_int(
                    _p49_summary.get("totalAccountarrear")
                    or _p49_summary.get("totalAccountInArrearGHS")
                )
                if _p49_arr is not None:
                    bureau_data["total_accounts_in_arrear"] = _p49_arr

        # ── Written-off within 36 months (HS-05) ─────────────────────────
        # Check BOTH Product 45 (creditAgreementSummary[]) and
        # Product 49 (detailedFacilityInfo[]) — same field names, different keys.
        # XDS dates are dd/mm/yyyy (e.g. "24/03/2026").
        from datetime import datetime as _dt, timedelta

        _cutoff = date.today() - timedelta(days=36 * 30)

        def _parse_xds_date(date_str: str) -> date | None:
            """Parse XDS date (dd/mm/yyyy) with fallbacks."""
            for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%Y/%m/%d"):
                try:
                    return _dt.strptime(date_str.strip()[:10], fmt).date()
                except (ValueError, TypeError):
                    continue
            return None

        _all_accounts: list[dict] = []
        _p45_accounts = _p45.get("creditAgreementSummary", [])
        if isinstance(_p45_accounts, list):
            _all_accounts.extend(a for a in _p45_accounts if isinstance(a, dict))
        _p49_accounts = _p49.get("detailedFacilityInfo", [])
        if isinstance(_p49_accounts, list):
            _all_accounts.extend(a for a in _p49_accounts if isinstance(a, dict))

        for _acct in _all_accounts:
            if _acct.get("accountStatusCode") != "W":
                continue
            _changed_str = _acct.get("changedOnDate") or _acct.get("closedDate", "")
            if not _changed_str:
                # No date — conservatively treat as recent
                bureau_data["has_written_off_within_36m"] = True
                break
            _parsed = _parse_xds_date(str(_changed_str))
            if _parsed is None:
                # Unparseable date — conservatively treat as recent
                bureau_data["has_written_off_within_36m"] = True
                break
            if _parsed >= _cutoff:
                bureau_data["has_written_off_within_36m"] = True
                break

        # ── Identity confidence (HS-03) ───────────────────────────────────
        # XDS confirms identity via national ID presence. Use 1.0 = verified,
        # 0.0 = not verified, matching the identity_verified feature definition.
        # Check P45 first, fall back to P49.
        _personal = _p45.get("personalDetailsSummary", _p45.get("personalDetails", {}))
        if isinstance(_personal, list) and _personal:
            _personal = _personal[0]
        if not isinstance(_personal, dict):
            _personal = {}
        if not _personal.get("nationalIDNo"):
            _p49_personal = _p49.get("personalDetailsSummary", _p49.get("personalDetails", {}))
            if isinstance(_p49_personal, list) and _p49_personal:
                _p49_personal = _p49_personal[0]
            if isinstance(_p49_personal, dict) and _p49_personal.get("nationalIDNo"):
                _personal = _p49_personal
        identity_confidence = 1.0 if _personal.get("nationalIDNo") else 0.0

    hard_stop_result = evaluate_hard_stops(
        applicant_age=applicant_age,
        identity_confidence=identity_confidence,
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

    # Apply reverse mapping from any frontend modifications
    features = payload_dict.get("bureau_data", {}).get("features")
    if features:
        from app.services.xds_reverse_mapper import apply_feature_overrides
        _raw_xds_data = apply_feature_overrides(_raw_xds_data, features)

    try:
        from app.core.events import get_event_publisher
        from app.config import get_settings

        publisher = get_event_publisher()
        settings = get_settings()

        de_payload = {
            "request_id": tracking_id,
            "data_source_id": tracking_id,
            "source_system": "xds",
            "models_to_run": ["all"],
            "xds_payload": {
                "consumer_full_report_45": _raw_xds_data.get("product_45") if _raw_xds_data else None,
                "consumer_mobile_report_49": _raw_xds_data.get("product_49") if _raw_xds_data else None
            }
        }
        
        # Publish to the DE Azure Function to trigger inferencing
        async def _publish_to_de_inference(payload: dict):
            import httpx

            url = settings.de_inference_url
            if settings.de_inference_code:
                url = f"{url}?code={settings.de_inference_code}"
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(url, json=payload)
                    if resp.status_code in (200, 201, 202):
                        logger.info(f"[scoring] Published score request {tracking_id} to DE inference function")
                    else:
                        logger.error(f"[scoring] DE inference call failed ({resp.status_code}): {resp.text}")
            except Exception as inner_e:
                logger.error(f"[scoring] DE inference call threw exception: {inner_e}")

        # Fire and forget
        asyncio.create_task(_publish_to_de_inference(de_payload))
        
    except Exception as e:
        logger.error(f"[scoring] Failed to trigger DE inference logic: {e}")
        score_req.status = ScoreRequestStatus.FAILED
        score_req.scored_at = utcnow()

        # Also create a ScoringResult row so the subscriber's idempotency
        # check stays consistent and GET endpoints return the error inline
        from app.models.scoring_result import ScoringDecision, ScoringResult

        error_result = ScoringResult(
            request_id=tracking_id,
            score_request_id=score_req.id,
            scoring_timestamp=utcnow(),
            decision=ScoringDecision.ERROR,
            condition_applied=[],
            scoring_metadata=None,
            errors=[str(e)],
        )
        db.add(error_result)
        # Wire the relationship so it's available for the response immediately
        score_req.scoring_result = error_result

    response = ScoreRequestResponse(
        request_id=tracking_id,
        reference_id=body.reference_id,
        status=score_req.status.value,
        scoring_result=score_req.scoring_result,
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
        select(ScoreRequest).options(selectinload(ScoreRequest.scoring_result)).where(
            ScoreRequest.tracking_id == request_id,
            ScoreRequest.organization_id == auth.organization_id,
        )
    )
    score_req = result.scalar_one_or_none()
    if not score_req:
        raise NotFoundError("Score request", request_id)

    return ScoreRequestResponse(
        request_id=score_req.tracking_id,
        reference_id=score_req.reference_id,
        status=score_req.status.value,
        scoring_result=score_req.scoring_result,
        timestamps={
            "requested_at": score_req.created_at.isoformat(),
            "scored_at": score_req.scored_at.isoformat() if score_req.scored_at else None,
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
    query = select(ScoreRequest).options(selectinload(ScoreRequest.scoring_result)).where(
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
            scoring_result=r.scoring_result,
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
