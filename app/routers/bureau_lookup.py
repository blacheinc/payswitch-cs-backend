"""Bureau Lookup router — XDS Credit Bureau data resolution endpoint.

Two-step scoring flow:
  1. POST /v1/bureau-lookup → Call XDS → return bureau data (or NO_RECORD defaults)
  2. User reviews → POST /v1/score-requests → hard-stops → DE → AI
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.dependencies import AuthContext, check_rate_limit
from app.schemas.bureau_schemas import (
    NO_RECORD_DEFAULT_FEATURES,
    BureauCreditAccount,
    BureauCreditSummary,
    BureauFeatures,
    BureauLookupRequest,
    BureauLookupResponse,
    BureauMetadata,
    BureauPersonalDetails,
)
from app.services.xds_client import XDSError, get_xds_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["Bureau Lookup"])


@router.post(
    "/bureau-lookup",
    response_model=BureauLookupResponse,
    summary="Look up credit bureau data for a customer",
    description=(
        "Step 1 of the scoring flow. Calls XDS Credit Bureau to fetch "
        "Products 45 + 49 data. Returns bureau features, credit summary, "
        "and hard-stop indicators. On NO_RECORD, returns default feature "
        "weights so the frontend can still render the form."
    ),
)
async def bureau_lookup(
    body: BureauLookupRequest,
    auth: Annotated[AuthContext, Depends(check_rate_limit)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BureauLookupResponse:
    """Call XDS Credit Bureau and return structured bureau data."""

    xds = get_xds_client()

    try:
        result = await xds.full_bureau_lookup(
            consumer_name=body.full_name,
            date_of_birth=body.date_of_birth,
            identification=body.identification,
            phone_number=body.phone_number,
            account_number=body.account_number,
            enquiry_reason=body.enquiry_reason,
        )
    except XDSError as e:
        logger.error(f"[bureau-lookup] XDS API error: {e.message}")
        raise HTTPException(
            status_code=502,
            detail=f"Credit bureau lookup failed: {e.message}",
        )
    except Exception as e:
        logger.exception("[bureau-lookup] Unexpected error")
        raise HTTPException(
            status_code=500,
            detail="Internal error during bureau lookup",
        )

    # Each product is an independent flow
    p45_data = result.get("product_45", {})
    p49_data = result.get("product_49", {})
    p45_status = p45_data.get("status", "NO_RECORD")
    p49_status = p49_data.get("status", "NO_RECORD")

    # If either product has MULTIPLE_MATCH, surface it to the caller
    if p45_status == "MULTIPLE_MATCH" or p49_status == "MULTIPLE_MATCH":
        p45_matches = p45_data.get("matches", [])
        p49_matches = p49_data.get("matches", [])
        return BureauLookupResponse(
            bureau_hit_status="MULTIPLE_MATCH",
            match_count=max(len(p45_matches), len(p49_matches)),
            features=None,
            metadata=BureauMetadata(
                bureau_hit_status="MULTIPLE_MATCH",
                product_source="45+49",
            ),
        )

    # If neither product has a HIT, return NO_RECORD
    if p45_status != "HIT" and p49_status != "HIT":
        return BureauLookupResponse(
            bureau_hit_status="NO_RECORD",
            match_count=0,
            features=NO_RECORD_DEFAULT_FEATURES,
            metadata=BureauMetadata(
                bureau_hit_status="NO_RECORD",
                score_grade="F",
                product_source="none",
            ),
        )

    # At least one product HIT — extract data from reports
    p45_report = p45_data.get("report") or {}
    p49_report = p49_data.get("report") or {}
    p45_match = p45_data.get("matches", [{}])[0] if p45_data.get("matches") else {}
    p49_match = p49_data.get("matches", [{}])[0] if p49_data.get("matches") else {}

    # Use p45 match as primary, fall back to p49
    primary_match = p45_match or p49_match

    # Determine product source
    if p45_status == "HIT" and p49_status == "HIT":
        product_source = "45+49"
    elif p45_status == "HIT":
        product_source = "45"
    else:
        product_source = "49"

    # Extract structured data from reports
    personal = _extract_personal_details(p45_report, primary_match)
    credit_summary = _extract_credit_summary(p45_report)
    credit_accounts = _extract_credit_accounts(p45_report)
    features = _extract_features(p45_report, p49_report, body.date_of_birth)

    applicant_age = _compute_age(body.date_of_birth)
    consumer_id = str(primary_match.get("consumerID", ""))
    metadata = BureauMetadata(
        bureau_hit_status="HIT",
        product_source=product_source,
        applicant_age_at_application=applicant_age,
    )

    # Cache raw XDS reports for the score-request → DE flow
    try:
        import json
        from app.storage.cache import get_redis_cache

        cache = get_redis_cache()
        cache_key = f"bureau:{consumer_id}"
        await cache._redis.set(
            cache_key,
            json.dumps({"product_45": p45_report, "product_49": p49_report}),
            ex=3600,  # 1 hour TTL
        )
        logger.debug(f"[bureau-lookup] Cached raw XDS data as {cache_key}")
    except Exception as e:
        logger.warning(f"[bureau-lookup] Failed to cache raw XDS data: {e}")

    return BureauLookupResponse(
        bureau_hit_status="HIT",
        match_count=1,
        personal_details=personal,
        credit_summary=credit_summary,
        credit_accounts=credit_accounts,
        features=features,
        metadata=metadata,
        enquiry_id=primary_match.get("enquiryID"),
        consumer_id=consumer_id,
    )


# ── Private helpers ──────────────────────────────────────────────────────────


def _extract_personal_details(
    p45: dict[str, Any], match: dict[str, Any]
) -> BureauPersonalDetails:
    """Extract personal details from Product 45 report."""
    personal = p45.get("personalDetailsSummary", p45.get("personalDetails", {}))
    if isinstance(personal, list) and personal:
        personal = personal[0]
    elif not isinstance(personal, dict):
        personal = {}

    return BureauPersonalDetails(
        consumer_id=match.get("consumerID"),
        first_name=personal.get("firstName", match.get("firstName")),
        surname=personal.get("surname", match.get("surname")),
        other_names=personal.get("otherNames", match.get("otherNames")),
        birth_date=personal.get("birthDate", match.get("birthDate")),
        gender=personal.get("gender"),
        national_id=personal.get("nationalIDNo", match.get("idNo")),
        phone=personal.get("cellularNo"),
        email=personal.get("emailAddress"),
        employer=personal.get("employerDetail"),
        address=personal.get("residentialAddress1"),
        marital_status=personal.get("maritalStatus"),
        dependants=personal.get("dependants"),
    )


def _extract_credit_summary(p45: dict[str, Any]) -> BureauCreditSummary | None:
    """Extract credit account summary from Product 45."""
    # Look for GHS summary
    summary = p45.get("creditAccountSummary", p45.get("creditAccountSummaryGHS", {}))
    if isinstance(summary, list) and summary:
        summary = summary[0]
    elif not isinstance(summary, dict):
        return None

    # Real API: top-level key "highestDelinquencyRating": {"highestDelinquencyRating": "0"}
    # Also check summary's own "rating" field
    delinquency_obj = p45.get("highestDelinquencyRating", {})
    if isinstance(delinquency_obj, list) and delinquency_obj:
        delinquency_obj = delinquency_obj[0]
    elif not isinstance(delinquency_obj, dict):
        delinquency_obj = {}
    del_rating = (
        delinquency_obj.get("highestDelinquencyRating")
        or summary.get("rating")
    )

    return BureauCreditSummary(
        total_accounts=_safe_int(summary.get("totalNumberofAccountsGHS")),
        total_monthly_instalment_ghs=_safe_float(summary.get("totalMonthlyInstalmentGHS")),
        total_outstanding_debt_ghs=_safe_float(summary.get("totalOutstandingdebtGHS")),
        total_accounts_in_arrear=_safe_int(summary.get("totalAccountInArrearGHS")),
        total_arrear_amount_ghs=_safe_float(summary.get("totalAmountInArrearGHS")),
        total_accounts_good_standing=_safe_int(summary.get("totalaccountinGoodconditionGHS")),
        total_judgements=_safe_int(summary.get("totalNumberofJudgementGHS")),
        total_judgement_amount_ghs=_safe_float(summary.get("totalJudgementAmountGHS")),
        total_dishonoured_cheques=_safe_int(summary.get("totalNumberofDishonouredGHS")),
        delinquency_rating=del_rating,
    )


def _extract_credit_accounts(p45: dict[str, Any]) -> list[BureauCreditAccount]:
    """Extract individual credit accounts from Product 45."""
    accounts_raw = p45.get("creditAgreementSummary", [])
    if not isinstance(accounts_raw, list):
        return []

    accounts = []
    for acct in accounts_raw:
        if not isinstance(acct, dict):
            continue
        # Get payment history if available
        payment_history = None
        payment_data = p45.get("accountMonthlyPaymentHistory", [])
        if isinstance(payment_data, list):
            for ph in payment_data:
                if isinstance(ph, dict) and ph.get("accountNo") == acct.get("accountNo"):
                    payment_history = [
                        ph.get(f"m{str(i).zfill(2)}", "#")
                        for i in range(1, 25)
                    ]
                    break

        accounts.append(
            BureauCreditAccount(
                subscriber_name=acct.get("subscriberName"),
                account_no=acct.get("accountNo"),
                date_opened=acct.get("dateAccountOpened"),
                facility_type=acct.get("indicatorDescription"),
                currency=acct.get("currency"),
                credit_limit=_safe_float(acct.get("openingBalanceAmt")),
                current_balance=_safe_float(acct.get("currentBalanceAmt")),
                instalment_amount=_safe_float(acct.get("instalmentAmount")),
                arrear_amount=_safe_float(acct.get("amountOverdue")),
                months_in_arrears=_safe_int(acct.get("monthsInArrears")),
                status_code=acct.get("accountStatusCode"),
                payment_history_24m=payment_history,
            )
        )

    return accounts


def _extract_features(
    p45: dict[str, Any], p49: dict[str, Any], dob: date
) -> BureauFeatures:
    """Extract and compute the 30 pre-binned features from bureau data.

    Note: In the full pipeline, the Data Engineer handles binning.
    This provides a preliminary feature extraction for display and
    for passing to the DE via the score request.
    """
    summary = p45.get("creditAccountSummary", p45.get("creditAccountSummaryGHS", {}))
    if isinstance(summary, list) and summary:
        summary = summary[0]
    elif not isinstance(summary, dict):
        summary = {}

    delinquency_obj = p45.get("highestDelinquencyRating", {})
    if isinstance(delinquency_obj, list) and delinquency_obj:
        delinquency_obj = delinquency_obj[0]
    elif not isinstance(delinquency_obj, dict):
        delinquency_obj = {}

    accounts_raw = p45.get("creditAgreementSummary", [])
    if not isinstance(accounts_raw, list):
        accounts_raw = []

    personal = p45.get("personalDetailsSummary", p45.get("personalDetails", {}))
    if isinstance(personal, list) and personal:
        personal = personal[0]
    elif not isinstance(personal, dict):
        personal = {}

    # Compute raw values (pre-binning — DE will re-bin these properly)
    total_accounts = _safe_int(summary.get("totalNumberofAccountsGHS")) or 0
    good_accounts = _safe_int(summary.get("totalaccountinGoodconditionGHS")) or 0
    arrear_accounts = _safe_int(summary.get("totalAccountInArrearGHS")) or 0
    total_debt = _safe_float(summary.get("totalOutstandingdebtGHS")) or 0
    total_arrear = _safe_float(summary.get("totalAmountInArrearGHS")) or 0
    total_instalment = _safe_float(summary.get("totalMonthlyInstalmentGHS")) or 0
    judgements = _safe_int(summary.get("totalNumberofJudgementGHS")) or 0
    bounced = _safe_int(summary.get("totalNumberofDishonouredGHS")) or 0

    # Check account statuses
    has_written = any(
        a.get("accountStatusCode") == "W"
        for a in accounts_raw
        if isinstance(a, dict)
    )
    has_charged = any(
        a.get("accountStatusCode") == "G"
        for a in accounts_raw
        if isinstance(a, dict)
    )
    has_legal = any(
        a.get("accountStatusCode") == "L"
        for a in accounts_raw
        if isinstance(a, dict)
    )

    # XDS docs specify text values: "Good", "Fairly Good", "Poor", "Very Poor"
    # Real API may also return numeric: "0"=good, "1"=fair, "2"=poor, "3"=very poor
    del_rating_str = str(delinquency_obj.get("highestDelinquencyRating", "")).strip().lower()
    delinquency_map = {
        # Official text values (from XDS API docs v2)
        "good": 1.0,
        "fairly good": 0.75,
        "poor": 0.35,
        "very poor": 0.0,
        # Numeric fallback (observed in real API responses)
        "0": 1.0, "1": 0.75, "2": 0.50, "3": 0.25, "4": 0.0,
    }
    del_score = delinquency_map.get(del_rating_str, 0.5)

    # Count product types for diversity
    product_types = set()
    for a in accounts_raw:
        if isinstance(a, dict) and a.get("indicatorDescription"):
            product_types.add(a["indicatorDescription"])

    # Defaults / adverse — real API uses "defaults" and "adverseDetails" keys
    defaults_raw = p45.get("defaults", p45.get("adverseDetails", []))
    has_adverse = len(defaults_raw) > 0 if isinstance(defaults_raw, list) else False

    # Enquiry history
    enquiries = p45.get("enquiryHistory", [])
    if not isinstance(enquiries, list):
        enquiries = []

    age = _compute_age(dob)

    return BureauFeatures(
        highest_delinquency_rating=del_score,
        has_active_arrears=0.0 if arrear_accounts > 0 else 1.0,
        total_arrear_amount_ghs=max(0.0, 1.0 - min(total_arrear / 10000, 1.0)) if total_arrear else 1.0,
        total_outstanding_debt_ghs=max(0.0, 1.0 - min(total_debt / 100000, 1.0)) if total_debt else 1.0,
        num_active_accounts=min(total_accounts / 10, 1.0) if total_accounts else 0.0,
        total_monthly_instalment_ghs=max(0.0, 1.0 - min(total_instalment / 5000, 1.0)) if total_instalment else 1.0,
        num_accounts_total=min(total_accounts / 15, 1.0) if total_accounts else 0.0,
        num_closed_accounts_good=min(good_accounts / 10, 1.0) if good_accounts else 0.0,
        product_diversity_score=min(len(product_types) / 5, 1.0),
        has_judgement=0.0 if judgements > 0 else 1.0,
        has_written_off=0.0 if has_written else 1.0,
        has_charged_off=0.0 if has_charged else 1.0,
        has_legal_handover=0.0 if has_legal else 1.0,
        num_bounced_cheques=max(0.0, 1.0 - min(bounced / 5, 1.0)) if bounced else 1.0,
        has_adverse_default=0.0 if has_adverse else 1.0,
        num_enquiries_12m=max(0.0, 1.0 - min(len(enquiries) / 10, 1.0)),
        num_enquiries_3m=max(0.0, 1.0 - min(len(enquiries) / 5, 1.0)),
        applicant_age=min(max(0.0, (age - 18) / 57), 1.0) if age else None,
        identity_verified=1.0 if personal.get("nationalIDNo") else 0.0,
        has_employer_detail=1.0 if personal.get("employerDetail") else 0.60,
        num_dependants=None,  # Comes from application
        # Placeholder values — DE will compute from payment history
        months_on_time_24m=None,
        worst_arrears_24m=None,
        current_streak_on_time=None,
        utilisation_ratio=None,
        credit_age_months=None,
        mobile_loan_history_count=None,
        mobile_max_loan_ghs=None,
        enquiry_reason_flags=None,
        address_stability=None,
    )


def _compute_age(dob: date) -> int | None:
    """Compute age from date of birth."""
    if not dob:
        return None
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _safe_int(val: Any) -> int | None:
    if val is None:
        return None
    try:
        # XDS returns comma-formatted strings like "4,800" — strip commas
        return int(str(val).replace(",", ""))
    except (ValueError, TypeError):
        return None


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        # XDS returns comma-formatted strings like "4,800.00" — strip commas
        return float(str(val).replace(",", ""))
    except (ValueError, TypeError):
        return None
