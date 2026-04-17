"""Shared scoring pipeline helpers.

Used by both the single score request router and the batch worker so the
hard-stop signal extraction and DE inference dispatch logic stay in one place.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


# ── Hard-stop signal extraction ──────────────────────────────────────────────


def extract_hard_stop_signals(
    raw_xds_data: dict[str, Any],
    date_of_birth: date | None = None,
) -> tuple[int | None, float | None, dict[str, Any]]:
    """Parse a raw XDS response into the three inputs that evaluate_hard_stops() needs.

    Returns:
        (applicant_age, identity_confidence, bureau_data)
    """
    p45 = raw_xds_data.get("product_45") or {}
    p49 = raw_xds_data.get("product_49") or {}

    bureau_data: dict[str, Any] = {}

    def _to_int(v: Any) -> int | None:
        try:
            return int(str(v).replace(",", "")) if v is not None else None
        except (ValueError, TypeError):
            return None

    def _parse_xds_date(date_str: str) -> date | None:
        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(date_str.strip()[:10], fmt).date()
            except (ValueError, TypeError):
                continue
        return None

    # ── Delinquency rating (HS-06) ────────────────────────────────────────
    _del_obj = (
        p45.get("highestDelinquencyRating")
        or p49.get("highestDelinquencyRating")
        or {}
    )
    if isinstance(_del_obj, list) and _del_obj:
        _del_obj = _del_obj[0]
    if isinstance(_del_obj, dict):
        bureau_data["delinquency_rating"] = _del_obj.get("highestDelinquencyRating")

    # ── Credit summary — judgements and arrears (HS-04, HS-07) ───────────
    _summary = p45.get("creditAccountSummary", p45.get("creditAccountSummaryGHS", {}))
    if isinstance(_summary, list) and _summary:
        _summary = _summary[0]
    if isinstance(_summary, dict):
        _jdg = _to_int(_summary.get("totalNumberofJudgementGHS"))
        _arr = _to_int(_summary.get("totalAccountInArrearGHS"))
        if _jdg is not None:
            bureau_data["total_judgements"] = _jdg
        if _arr is not None:
            bureau_data["total_accounts_in_arrear"] = _arr

    # Fall back to P49 summary for arrears when P45 didn't provide a value
    if "total_accounts_in_arrear" not in bureau_data:
        _p49_summary = p49.get("creditAccountSummary", {})
        if isinstance(_p49_summary, list) and _p49_summary:
            _p49_summary = _p49_summary[0]
        if isinstance(_p49_summary, dict):
            _p49_arr = _to_int(
                _p49_summary.get("totalAccountarrear")
                or _p49_summary.get("totalAccountInArrearGHS")
            )
            if _p49_arr is not None:
                bureau_data["total_accounts_in_arrear"] = _p49_arr

    # ── Written-off within 36 months (HS-05) ─────────────────────────────
    _cutoff = date.today() - timedelta(days=36 * 30)
    _all_accounts: list[dict] = []
    _p45_accounts = p45.get("creditAgreementSummary", [])
    if isinstance(_p45_accounts, list):
        _all_accounts.extend(a for a in _p45_accounts if isinstance(a, dict))
    _p49_accounts = p49.get("detailedFacilityInfo", [])
    if isinstance(_p49_accounts, list):
        _all_accounts.extend(a for a in _p49_accounts if isinstance(a, dict))

    for _acct in _all_accounts:
        if _acct.get("accountStatusCode") != "W":
            continue
        _changed_str = _acct.get("changedOnDate") or _acct.get("closedDate", "")
        if not _changed_str:
            bureau_data["has_written_off_within_36m"] = True
            break
        _parsed = _parse_xds_date(str(_changed_str))
        if _parsed is None or _parsed >= _cutoff:
            bureau_data["has_written_off_within_36m"] = True
            break

    # ── Identity confidence (HS-03) ───────────────────────────────────────
    _personal = p45.get("personalDetailsSummary", p45.get("personalDetails", {}))
    if isinstance(_personal, list) and _personal:
        _personal = _personal[0]
    if not isinstance(_personal, dict):
        _personal = {}

    if not _personal.get("nationalIDNo"):
        _p49_personal = p49.get("personalDetailsSummary", p49.get("personalDetails", {}))
        if isinstance(_p49_personal, list) and _p49_personal:
            _p49_personal = _p49_personal[0]
        if isinstance(_p49_personal, dict) and _p49_personal.get("nationalIDNo"):
            _personal = _p49_personal

    identity_confidence: float | None = None
    if _personal.get("nationalIDNo"):
        identity_confidence = 1.0
    else:
        identity_confidence = 0.0

    # ── Applicant age ─────────────────────────────────────────────────────
    applicant_age: int | None = None
    _p45_pd = p45.get("personalDetailsSummary", p45.get("personalDetails", {}))
    if isinstance(_p45_pd, list) and _p45_pd:
        _p45_pd = _p45_pd[0]
    if isinstance(_p45_pd, dict):
        _bd_str = _p45_pd.get("birthDate", "")
        if _bd_str:
            _bd = _parse_xds_date(str(_bd_str))
            if _bd:
                today = date.today()
                applicant_age = (
                    today.year - _bd.year
                    - ((today.month, today.day) < (_bd.month, _bd.day))
                )

    # Fall back to the DOB the caller provided if XDS didn't return one
    if applicant_age is None and date_of_birth:
        today = date.today()
        applicant_age = (
            today.year - date_of_birth.year
            - ((today.month, today.day) < (date_of_birth.month, date_of_birth.day))
        )

    return applicant_age, identity_confidence, bureau_data


def build_de_payload(tracking_id: str, raw_xds_data: dict[str, Any]) -> dict[str, Any]:
    """Build the payload sent to the DE inference endpoint."""
    return {
        "request_id": tracking_id,
        "data_source_id": tracking_id,
        "source_system": "xds",
        "models_to_run": ["all"],
        "xds_payload": {
            "consumer_full_report_45": raw_xds_data.get("product_45"),
            "consumer_mobile_report_49": raw_xds_data.get("product_49"),
        },
    }


async def fire_de_inference(tracking_id: str, de_payload: dict[str, Any]) -> None:
    """POST the scoring payload to the DE inference endpoint (fire-and-forget).

    Errors are logged but not re-raised — the caller is responsible for
    handling the ScoreRequest status if this fails.
    """
    import httpx

    from app.config import get_settings

    settings = get_settings()
    url = settings.de_inference_url
    if settings.de_inference_code:
        url = f"{url}?code={settings.de_inference_code}"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=de_payload)
            if resp.status_code in (200, 201, 202):
                logger.info(
                    "[pipeline] Published %s to DE inference", tracking_id
                )
            else:
                logger.error(
                    "[pipeline] DE inference call failed (%d): %s",
                    resp.status_code, resp.text,
                )
    except Exception as e:
        logger.error("[pipeline] DE inference call threw exception: %s", e)
        raise
