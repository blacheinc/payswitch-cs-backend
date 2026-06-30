"""Bureau Lookup router — XDS Credit Bureau data resolution endpoint.

Two-step scoring flow:
  1. POST /v1/bureau-lookup → Call XDS → return bureau data (or NO_RECORD defaults)
  2. User reviews → POST /v1/score-requests → hard-stops → DE → AI
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.dependencies import AuthContext, require_permission
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
    auth: Annotated[AuthContext, Depends(require_permission("bureau.lookup"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BureauLookupResponse:
    """Call XDS Credit Bureau and return structured bureau data."""

    # ── Check cache first to avoid redundant XDS API calls ────────────
    import json as _json

    from app.storage.cache import get_redis_cache

    _cached_result: dict | None = None
    _cache_lookup_id = body.identification or body.phone_number
    _cache_key_id = f"bureau:id:{_cache_lookup_id}" if _cache_lookup_id else None

    # ── Demo simulation: serve a stored XDS report instead of calling XDS ──
    from app.config import get_settings as _get_settings

    _settings = _get_settings()
    if _settings.demo_simulation_mode:
        import asyncio as _asyncio
        import secrets as _secrets

        from app.demo.simulation_records import get_simulation_report

        # Feel like a real bureau call rather than an instant lookup.
        _delay = max(
            0.0,
            _settings.demo_bureau_delay_seconds + _secrets.SystemRandom().uniform(-0.4, 0.6),
        )
        if _delay:
            await _asyncio.sleep(_delay)

        _sim_report = get_simulation_report(body.identification, body.phone_number)
        if _sim_report is None:
            # Sandboxed: any ID that isn't one of the demo records → no record.
            logger.info("[bureau-lookup] SIMULATION: no demo record for %s", _cache_lookup_id)
            return BureauLookupResponse(
                bureau_hit_status="NO_RECORD",
                match_count=0,
                features=NO_RECORD_DEFAULT_FEATURES,
                metadata=BureauMetadata(
                    bureau_hit_status="NO_RECORD", score_grade="F", product_source="none"
                ),
            )
        logger.info("[bureau-lookup] SIMULATION hit for %s", _cache_lookup_id)
        _cached_result = {"product_45": _sim_report, "product_49": {}}

    if _cached_result is None and _cache_key_id:
        try:
            _cache = get_redis_cache()
            _cached_bytes = await _cache.get(_cache_key_id)
            if _cached_bytes:
                _cached_result = _json.loads(_cached_bytes)
                logger.info(
                    f"[bureau-lookup] Cache HIT for {_cache_key_id} — skipping XDS API call"
                )
        except Exception as e:
            logger.warning(f"[bureau-lookup] Cache check failed: {e}")

    if _cached_result:
        # Reconstruct the result dict from cached data
        result = {
            "product_45": {
                "status": "HIT" if _cached_result.get("product_45") else "NO_RECORD",
                "report": _cached_result.get("product_45", {}),
                "matches": [_cached_result.get("product_45", {}).get("personalDetailsSummary", {})],
            },
            "product_49": {
                "status": "HIT" if _cached_result.get("product_49") else "NO_RECORD",
                "report": _cached_result.get("product_49", {}),
                "matches": [_cached_result.get("product_49", {}).get("personalDetailsSummary", {})],
            },
        }
    else:
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
        except httpx.TimeoutException:
            logger.error("[bureau-lookup] Connection timeout reaching XDS API")
            raise HTTPException(
                status_code=504,
                detail="Credit bureau API is unreachable (connection timeout)",
            )
        except Exception as e:
            logger.exception("[bureau-lookup] Unexpected error")
            raise HTTPException(
                status_code=500,
                detail=f"Internal error during bureau lookup: {type(e).__name__}: {e}",
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
    credit_accounts = _extract_credit_accounts(p45_report, p49_report)
    features = _extract_features(p45_report, p49_report)

    applicant_age = features.applicant_age
    # Extract consumer_id from the report (available in both live and cached paths)
    _p45_personal = p45_report.get("personalDetailsSummary", {})
    if isinstance(_p45_personal, list) and _p45_personal:
        _p45_personal = _p45_personal[0]
    _p49_personal = p49_report.get("personalDetailsSummary", {})
    if isinstance(_p49_personal, list) and _p49_personal:
        _p49_personal = _p49_personal[0]
    consumer_id = str(
        (_p45_personal or {}).get("consumerID", "")
        or (_p49_personal or {}).get("consumerID", "")
        or primary_match.get("consumerID", "")
    )
    metadata = BureauMetadata(
        bureau_hit_status="HIT",
        product_source=product_source,
        applicant_age_at_application=applicant_age,
    )

    # Cache raw XDS reports for both the score-request flow (by consumer_id)
    # and the cache-first bureau lookup (by identification/national ID)
    try:
        _cache = get_redis_cache()
        _xds_json = _json.dumps({"product_45": p45_report, "product_49": p49_report})
        _ttl = 86400  # 24 hour TTL — must outlast the review window

        # Primary key: by consumer_id (used by score-request → DE flow)
        cache_key = f"bureau:{consumer_id}"
        await _cache.set(cache_key, _xds_json, ttl=_ttl)

        # Secondary key: by identification/phone (used for cache-first lookups)
        if _cache_lookup_id:
            await _cache.set(f"bureau:id:{_cache_lookup_id}", _xds_json, ttl=_ttl)

        logger.debug(f"[bureau-lookup] Cached raw XDS data as {cache_key}")
    except Exception as e:
        logger.warning(f"[bureau-lookup] Failed to cache raw XDS data: {e}")

    # Extract enquiry_id from the report (available in both live and cached paths)
    _enquiry_details = p45_report.get("enquiryDetails", {}) or p49_report.get("enquiryDetails", {})
    if isinstance(_enquiry_details, list) and _enquiry_details:
        _enquiry_details = _enquiry_details[0]
    if not isinstance(_enquiry_details, dict):
        _enquiry_details = {}
    _enquiry_id = str(_enquiry_details.get("subscriberEnquiryResultID", ""))

    return BureauLookupResponse(
        bureau_hit_status="HIT",
        match_count=1,
        personal_details=personal,
        credit_summary=credit_summary,
        credit_accounts=credit_accounts,
        features=features,
        metadata=metadata,
        enquiry_id=_enquiry_id,
        consumer_id=consumer_id,
    )


# ── Private helpers ──────────────────────────────────────────────────────────


def _extract_personal_details(p45: dict[str, Any], match: dict[str, Any]) -> BureauPersonalDetails:
    """Extract personal details from Product 45 report."""
    personal = p45.get("personalDetailsSummary", p45.get("personalDetails", {}))
    if isinstance(personal, list) and personal:
        personal = personal[0]
    elif not isinstance(personal, dict):
        personal = {}

    return BureauPersonalDetails(
        consumer_id=str(match.get("consumerID", "")) if match.get("consumerID") else "",
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
    del_rating = delinquency_obj.get("highestDelinquencyRating") or summary.get("rating")

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


def _extract_credit_accounts(
    p45: dict[str, Any], p49: dict[str, Any] | None = None
) -> list[BureauCreditAccount]:
    """Extract individual credit accounts from Products 45 and 49.

    Product 45 stores accounts in ``creditAgreementSummary[]``.
    Product 49 stores accounts in ``detailedFacilityInfo[]``.
    Both use the same field names (accountNo, accountStatusCode, etc.).
    """
    accounts: list[BureauCreditAccount] = []

    # ── Product 45 accounts (creditAgreementSummary) ────────────────────
    p45_accounts = p45.get("creditAgreementSummary", [])
    if not isinstance(p45_accounts, list):
        p45_accounts = []

    for acct in p45_accounts:
        if not isinstance(acct, dict):
            continue
        # Get payment history if available
        payment_history = None
        payment_data = p45.get("accountMonthlyPaymentHistory", [])
        if isinstance(payment_data, list):
            for ph in payment_data:
                if isinstance(ph, dict) and ph.get("accountNo") == acct.get("accountNo"):
                    payment_history = [ph.get(f"m{str(i).zfill(2)}", "#") for i in range(1, 25)]
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

    # ── Product 49 accounts (detailedFacilityInfo) ──────────────────────
    if p49:
        p49_accounts = p49.get("detailedFacilityInfo", [])
        if not isinstance(p49_accounts, list):
            p49_accounts = []

        # Avoid duplicates: track account numbers already added from P45
        seen_account_nos = {a.account_no for a in accounts if a.account_no}

        for acct in p49_accounts:
            if not isinstance(acct, dict):
                continue
            acct_no = acct.get("accountNo")
            if acct_no in seen_account_nos:
                continue

            accounts.append(
                BureauCreditAccount(
                    subscriber_name=acct.get("subscriberName"),
                    account_no=acct_no,
                    date_opened=acct.get("dateAccountOpened"),
                    facility_type=acct.get("indicatorDescription"),
                    currency=acct.get("currency"),
                    credit_limit=_safe_float(acct.get("openingBalanceAmt")),
                    current_balance=_safe_float(acct.get("currentBalanceAmt")),
                    instalment_amount=_safe_float(acct.get("instalmentAmount")),
                    arrear_amount=_safe_float(acct.get("amountOverdue")),
                    months_in_arrears=_safe_int(acct.get("monthsInArrears")),
                    status_code=acct.get("accountStatusCode"),
                    payment_history_24m=None,  # P49 has no payment history
                )
            )

    return accounts


def _extract_features(p45: dict[str, Any], p49: dict[str, Any]) -> BureauFeatures:
    """Extract the 30 raw feature values exclusively from XDS product 45/49.

    ALL data comes from the XDS report — nothing from the request body.
    Values are raw (not binned) — the DE pipeline handles normalisation.
    See docs/de_feature_mapping.md for the full forward/reverse mapping.
    """
    from datetime import datetime as _dt
    from datetime import timedelta

    # ── Summary data (prefer P45, fall back to P49) ─────────────────────
    summary = p45.get("creditAccountSummary", p45.get("creditAccountSummaryGHS", {}))
    if isinstance(summary, list) and summary:
        summary = summary[0]
    elif not isinstance(summary, dict):
        summary = {}

    # P49 summary uses different field names (no GHS suffix, typos like "God")
    p49_summary = p49.get("creditAccountSummary", {}) if p49 else {}
    if isinstance(p49_summary, list) and p49_summary:
        p49_summary = p49_summary[0]
    elif not isinstance(p49_summary, dict):
        p49_summary = {}

    delinquency_obj = (
        p45.get("highestDelinquencyRating")
        or (p49.get("highestDelinquencyRating") if p49 else None)
        or {}
    )
    if isinstance(delinquency_obj, list) and delinquency_obj:
        delinquency_obj = delinquency_obj[0]
    elif not isinstance(delinquency_obj, dict):
        delinquency_obj = {}

    # Pass through the raw XDS delinquency rating string as-is
    del_raw = str(delinquency_obj.get("highestDelinquencyRating", "")).strip() or None

    # ── Combine all accounts from both products ─────────────────────────
    # P45: creditAgreementSummary[], P49: detailedFacilityInfo[]
    accounts_raw: list[dict[str, Any]] = []
    p45_accounts = p45.get("creditAgreementSummary", [])
    if isinstance(p45_accounts, list):
        accounts_raw.extend(a for a in p45_accounts if isinstance(a, dict))
    p49_accounts = p49.get("detailedFacilityInfo", []) if p49 else []
    if isinstance(p49_accounts, list):
        accounts_raw.extend(a for a in p49_accounts if isinstance(a, dict))

    personal = p45.get("personalDetailsSummary", p45.get("personalDetails", {}))
    if isinstance(personal, list) and personal:
        personal = personal[0]
    elif not isinstance(personal, dict):
        personal = {}
    # Fall back to P49 personal details for identity
    if not personal.get("nationalIDNo") and p49:
        p49_personal = p49.get("personalDetailsSummary", p49.get("personalDetails", {}))
        if isinstance(p49_personal, list) and p49_personal:
            p49_personal = p49_personal[0]
        if isinstance(p49_personal, dict) and p49_personal.get("nationalIDNo"):
            personal = p49_personal

    # applicant_age — rename of 45:personalDetailsSummary.birthDate (raw string)
    applicant_age_raw = str(personal.get("birthDate", "")).strip() or None

    # ── str passthrough features — raw XDS strings for reverse mapping ──
    # num_dependants — raw from 45:personalDetailsSummary.dependants
    num_dependants_raw = str(personal.get("dependants", "")).strip() or None
    # identity_verified — raw from 45:personalDetailsSummary.nationalIDNo
    identity_verified_raw = str(personal.get("nationalIDNo", "")).strip()
    # has_employer_detail — raw from 45:personalDetailsSummary.employerDetail
    has_employer_detail_raw = str(personal.get("employerDetail", "")).strip()

    # Credit account summary — pass raw XDS strings (prefer P45, fall back P49)
    total_accounts_raw = (
        summary.get("totalNumberofAccountsGHS")
        or p49_summary.get("totalNumberofAccountsGHS")
        or None
    )
    active_accounts_raw = (
        summary.get("totalActiveAccountsGHS")
        or p49_summary.get("totalNumberofActiveAccountsGHS")
        or None
    )
    arrear_accounts_raw = (
        summary.get("totalAccountInArrearGHS") or p49_summary.get("totalAccountarrear") or None
    )
    total_debt_raw = (
        summary.get("totalOutstandingdebtGHS") or p49_summary.get("totalOutstandingdebt") or None
    )
    total_arrear_raw = (
        summary.get("totalAmountInArrearGHS") or p49_summary.get("amountarrear") or None
    )
    # total_monthly_instalment_ghs — P45 value (str); if P49 also has one, keep P45
    instalment_raw = (
        summary.get("totalMonthlyInstalmentGHS")
        or p49_summary.get("totalMonthlyInstalment")
        or None
    )

    # num_bounced_cheques — raw string, with 45:dudCheqEventSummary fallback
    bounced_raw = summary.get("totalNumberofDishonouredGHS")
    if bounced_raw is not None and _safe_int(bounced_raw) == 0:
        dud_cheques = p45.get("dudCheqEventSummary", [])
        if isinstance(dud_cheques, list) and len(dud_cheques) > 0:
            bounced_raw = str(len(dud_cheques))

    # has_judgement — from 45:judgementSummary (non-empty array)
    judgement_list = p45.get("judgementSummary", [])
    has_judgement_val = 1 if (isinstance(judgement_list, list) and len(judgement_list) > 0) else 0

    # Check account statuses across BOTH products
    has_written = any(a.get("accountStatusCode") == "W" for a in accounts_raw)
    has_charged = any(a.get("accountStatusCode") == "G" for a in accounts_raw)
    has_legal = any(a.get("accountStatusCode") == "L" for a in accounts_raw)

    # num_closed_accounts_good — closed with non-adverse status + closedDate
    bad_statuses = {"W", "G", "L"}
    closed_good = sum(
        1
        for a in accounts_raw
        if a.get("closedDate") and a.get("accountStatusCode") not in bad_statuses
    )

    # product_diversity_score — unique types via accountType/loanType/subscriberName
    product_types = set()
    for a in accounts_raw:
        ptype = (
            a.get("accountType")
            or a.get("loanType")
            or a.get("subscriberName")
            or a.get("indicatorDescription")
        )
        if ptype:
            product_types.add(ptype)

    # has_adverse_default — from 45:defaults OR 45:adverseDetails (non-empty)
    defaults_raw = p45.get("defaults", [])
    adverse_raw = p45.get("adverseDetails", [])
    has_adverse = (isinstance(defaults_raw, list) and len(defaults_raw) > 0) or (
        isinstance(adverse_raw, list) and len(adverse_raw) > 0
    )

    # ── Payment history features (45:accountMonthlyPaymentHistory) ──────
    # m01–m24: "#" = not reported, "0" = on time, "1"+ = months in arrears
    payment_histories = p45.get("accountMonthlyPaymentHistory", [])
    if not isinstance(payment_histories, list):
        payment_histories = []

    all_monthly: list[int | None] = []
    for ph in payment_histories:
        if not isinstance(ph, dict):
            continue
        for i in range(1, 25):
            val = str(ph.get(f"m{i:02d}", "#")).strip()
            if val in ("#", "", "None"):
                all_monthly.append(None)
            else:
                try:
                    all_monthly.append(int(val))
                except ValueError:
                    all_monthly.append(None)

    reported = [s for s in all_monthly if s is not None]
    months_on_time = sum(1 for s in reported if s == 0) if reported else None

    # worst_arrears_24m — find max monthsInArrears across all accounts, return raw string
    worst_arrears_int: int | None = max(reported) if reported else None
    for a in accounts_raw:
        mia = _safe_int(a.get("monthsInArrears"))
        if mia is not None:
            worst_arrears_int = max(worst_arrears_int or 0, mia)
    worst_arrears_raw = str(worst_arrears_int) if worst_arrears_int is not None else None

    current_streak: int | None = None
    if reported:
        current_streak = 0
        for s in all_monthly:
            if s == 0:
                current_streak += 1
            elif s is None:
                continue
            else:
                break

    # ── Utilisation ratio ───────────────────────────────────────────────
    total_opening = sum((_safe_float(a.get("openingBalanceAmt")) or 0) for a in accounts_raw)
    total_current = sum((_safe_float(a.get("currentBalanceAmt")) or 0) for a in accounts_raw)
    utilisation_ratio = round(total_current / total_opening, 4) if total_opening > 0 else None

    # ── Credit age — oldest dateAccountOpened raw string ─────────────────
    credit_age_raw: str | None = None
    oldest_date = None
    for a in accounts_raw:
        opened_str = a.get("dateAccountOpened", "")
        if not opened_str:
            continue
        for _fmt in ("%d/%m/%Y", "%Y-%m-%d"):
            try:
                opened_date = _dt.strptime(str(opened_str).strip()[:10], _fmt).date()
                if oldest_date is None or opened_date < oldest_date:
                    oldest_date = opened_date
                    credit_age_raw = str(opened_str).strip()
                break
            except (ValueError, TypeError):
                continue

    # ── Enquiry history — combine both products, deduplicate by ID ──────
    enquiries = p45.get("enquiryHistory", [])
    if not isinstance(enquiries, list):
        enquiries = []
    p49_enquiries = p49.get("enquiryHistory", []) if p49 else []
    if isinstance(p49_enquiries, list) and p49_enquiries:
        seen_ids = {e.get("subscriberEnquiryResultID") for e in enquiries if isinstance(e, dict)}
        for e in p49_enquiries:
            if isinstance(e, dict) and e.get("subscriberEnquiryResultID") not in seen_ids:
                enquiries.append(e)

    _today = date.today()
    _3m_ago = _today - timedelta(days=90)
    _12m_ago = _today - timedelta(days=365)
    enquiries_3m = 0
    enquiries_12m = 0
    for eq in enquiries:
        if not isinstance(eq, dict):
            continue
        eq_date_str = eq.get("dateRequested", "")
        eq_date = None
        for _fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y", "%Y-%m-%d"):
            try:
                eq_date = _dt.strptime(str(eq_date_str).strip()[:19], _fmt).date()
                break
            except (ValueError, TypeError):
                continue
        if eq_date:
            if eq_date >= _3m_ago:
                enquiries_3m += 1
            if eq_date >= _12m_ago:
                enquiries_12m += 1

    # ── Address stability (45:addressHistory) ───────────────────────────
    address_history = p45.get("addressHistory", [])
    address_stability: int | None = None
    if isinstance(address_history, list) and address_history:
        unique_addrs = set()
        for ah in address_history:
            if not isinstance(ah, dict):
                continue
            addr = ah.get("address") or ah.get("address1", "")
            if addr and str(addr).strip():
                unique_addrs.add(str(addr).strip().upper())
        address_stability = len(unique_addrs) if unique_addrs else None

    # ── Mobile loan features from Product 49 ────────────────────────────
    mobile_loan_count: int | None = None
    if p49_accounts and isinstance(p49_accounts, list):
        mobile_accts = [a for a in p49_accounts if isinstance(a, dict)]
        if mobile_accts:
            mobile_loan_count = len(mobile_accts)
    # mobile_max_loan_ghs — raw string from 49:creditAccountSummary.highestAmountTaken
    mobile_max_loan_raw = p49_summary.get("highestAmountTaken") if p49_summary else None

    return BureauFeatures(
        # ── All features are str — renames of XDS fields ──────────────
        highest_delinquency_rating=del_raw,
        has_active_arrears=arrear_accounts_raw,
        total_arrear_amount_ghs=total_arrear_raw,
        total_outstanding_debt_ghs=total_debt_raw,
        num_active_accounts=active_accounts_raw,
        total_monthly_instalment_ghs=instalment_raw,
        num_accounts_total=total_accounts_raw,
        num_bounced_cheques=bounced_raw,
        mobile_max_loan_ghs=mobile_max_loan_raw,
        worst_arrears_24m=worst_arrears_raw,
        num_dependants=num_dependants_raw,
        identity_verified=identity_verified_raw,
        has_employer_detail=has_employer_detail_raw,
        applicant_age=applicant_age_raw,
        credit_age_months=credit_age_raw,
        months_on_time_24m=str(months_on_time) if months_on_time is not None else None,
        current_streak_on_time=str(current_streak) if current_streak is not None else None,
        utilisation_ratio=str(utilisation_ratio) if utilisation_ratio is not None else None,
        num_closed_accounts_good=str(closed_good),
        product_diversity_score=str(len(product_types)),
        mobile_loan_history_count=str(mobile_loan_count) if mobile_loan_count is not None else None,
        has_judgement=str(has_judgement_val),
        has_written_off=has_written,
        has_charged_off=has_charged,
        has_legal_handover=has_legal,
        has_adverse_default=has_adverse,
        num_enquiries_3m=str(enquiries_3m),
        num_enquiries_12m=str(enquiries_12m),
        enquiry_reason_flags="1" if len(enquiries) > 0 else "0",
        address_stability=str(address_stability) if address_stability is not None else None,
    )


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
