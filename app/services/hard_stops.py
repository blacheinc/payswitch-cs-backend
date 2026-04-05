"""Hard-stop rules for pre-screening applicants before sending to the AI pipeline.

Per Section 5.2 of the contract, the Backend Engineer is responsible for these checks.
If any hard-stop triggers, the request is immediately declined without hitting the ML pipeline.

Hard-stop conditions:
1. Underage: age < 18
2. Over-age: age > 75
3. Identity confidence < 0.70 (KYC fail)
4. Court judgement present
5. Written-off account within 36 months
6. Delinquency rating = 4 (worst category) within 12 months
7. 3+ accounts simultaneously in arrears
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class HardStopResult:
    """Result of hard-stop evaluation."""

    passed: bool = True
    triggered_rules: list[str] = field(default_factory=list)
    decline_reasons: list[str] = field(default_factory=list)

    def fail(self, rule_code: str, reason: str) -> None:
        self.passed = False
        self.triggered_rules.append(rule_code)
        self.decline_reasons.append(reason)


def evaluate_hard_stops(
    applicant_age: int | None = None,
    identity_confidence: float | None = None,
    bureau_data: dict[str, Any] | None = None,
) -> HardStopResult:
    """Evaluate all hard-stop rules against the applicant's data.

    Args:
        applicant_age: Age of the applicant in years.
        identity_confidence: KYC identity verification confidence (0-1).
        bureau_data: Parsed bureau report data with credit summary and account details.

    Returns:
        HardStopResult indicating whether the applicant passed all checks.
    """
    result = HardStopResult()
    bureau = bureau_data or {}

    # ── Rule 1: Underage ─────────────────────────────────────────────────
    if applicant_age is not None and applicant_age < 18:
        result.fail("HS-01", f"Applicant is underage ({applicant_age} years)")

    # ── Rule 2: Over-age ─────────────────────────────────────────────────
    if applicant_age is not None and applicant_age > 75:
        result.fail("HS-02", f"Applicant exceeds maximum age ({applicant_age} years)")

    # ── Rule 3: Identity confidence < 0.70 (KYC fail) ───────────────────
    if identity_confidence is not None and identity_confidence < 0.70:
        result.fail(
            "HS-03",
            f"Identity confidence too low ({identity_confidence:.2f} < 0.70)",
        )

    # ── Rule 4: Court judgement present ──────────────────────────────────
    total_judgements = bureau.get("total_judgements", 0)
    if total_judgements and int(total_judgements) > 0:
        result.fail(
            "HS-04",
            f"Court judgement(s) present ({total_judgements})",
        )

    # ── Rule 5: Written-off account within 36 months ────────────────────
    has_written_off_36m = bureau.get("has_written_off_within_36m", False)
    if has_written_off_36m:
        result.fail("HS-05", "Written-off account within 36 months")

    # ── Rule 6: Delinquency rating = 4 ("Very Poor") within 12 months ──────
    # XDS returns either a numeric string ("0"-"4") or the text values documented
    # in the XDS API: "Good", "Fairly Good", "Poor", "Very Poor".
    # Only the worst category — numeric 4 / text "Very Poor" — triggers a hard stop.
    delinquency_rating = bureau.get("delinquency_rating", "")
    if delinquency_rating:
        rating_str = str(delinquency_rating).strip()
        try:
            if int(rating_str) == 4:
                result.fail("HS-06", f"Delinquency rating is {rating_str} (Very Poor / worst)")
        except (ValueError, TypeError):
            if rating_str.lower() == "very poor":
                result.fail("HS-06", "Delinquency rating is 'Very Poor' (worst category)")

    # ── Rule 7: 3+ accounts simultaneously in arrears ───────────────────
    accounts_in_arrear = bureau.get("total_accounts_in_arrear", 0)
    if accounts_in_arrear and int(accounts_in_arrear) >= 3:
        result.fail(
            "HS-07",
            f"Too many accounts in arrears ({accounts_in_arrear} >= 3)",
        )

    if not result.passed:
        logger.info(
            f"[HardStop] DECLINED — rules triggered: {result.triggered_rules}"
        )
    else:
        logger.debug("[HardStop] All checks passed")

    return result
