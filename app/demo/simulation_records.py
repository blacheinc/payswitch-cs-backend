"""Deterministic demo records — bureau lookup + scoring simulation.

When ``settings.demo_simulation_mode`` is on, the bureau-lookup and
score-request endpoints serve these stored answers instead of calling XDS or
the AI pipeline. Same input → same output, every time, with no external
dependency. Survives a database wipe because it lives in code.

Five records, each keyed by the ID the client types into the form. The raw XDS
reports (real, dummified) live in ``xds_fixtures.json``; the deterministic
scoring outcome for each lives here.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_FIXTURES_PATH = Path(__file__).with_name("xds_fixtures.json")


@lru_cache(maxsize=1)
def _xds_fixtures() -> dict[str, dict]:
    """Raw XDS reports keyed by identification number."""
    return json.loads(_FIXTURES_PATH.read_text())


# ── The five deterministic outcomes ─────────────────────────────────────────
# Each ``outcome`` is shaped exactly like what the AI pipeline would emit, so it
# drops straight into ScoringResult / DecisionMetric and renders on the
# dashboards (decision, score, risk tier, fraud, recommended amount, reasons).

SIMULATION_RECORDS: list[dict[str, Any]] = [
    {
        "identification": "FUW3142495073",
        "full_name": "Kofi Owusu",
        "date_of_birth": "1966-12-15",
        "phone": "2330615394",
        "label": "Excellent — clean, established credit",
        "outcome": {
            "decision": "APPROVE",
            "credit_score": 781,
            "score_grade": "A",
            "risk_tier": "LOW",
            "probability_of_default": 0.04,
            "pd_confidence": 0.95,
            "fraud_risk_flag": "LOW",
            "fraud_anomaly_score": 0.05,
            "recommended_amount_ghs": None,        # full requested amount approved
            "recommended_loan_tier": "LARGE",
            "income_tier": 3,
            "income_tier_label": "UPPER",
            "income_confidence": 0.93,
            "data_quality_score": 0.96,
            "conditions": [],
            "decision_reason_codes": ["R01", "R04"],
            "shap_contributions": [
                {"feature": "payment_history_strength", "value": 0.21, "direction": "positive"},
                {"feature": "credit_utilization", "value": 0.14, "direction": "positive"},
                {"feature": "account_age_months", "value": 0.11, "direction": "positive"},
            ],
        },
    },
    {
        "identification": "NNG3588016011",
        "full_name": "Fiifi Quaye",
        "date_of_birth": "1961-05-03",
        "phone": "0244617311",
        "label": "Good but thin file — approve with conditions",
        "outcome": {
            "decision": "CONDITIONAL_APPROVE",
            "credit_score": 668,
            "score_grade": "B",
            "risk_tier": "MEDIUM",
            "probability_of_default": 0.12,
            "pd_confidence": 0.84,
            "fraud_risk_flag": "LOW",
            "fraud_anomaly_score": 0.09,
            "recommended_amount_ghs": None,        # FE shows reduced amount via tier
            "recommended_loan_tier": "MEDIUM",
            "income_tier": 2,
            "income_tier_label": "UPPER_MID",
            "income_confidence": 0.81,
            "data_quality_score": 0.88,
            "conditions": ["guarantor_required", "reduced_amount"],
            "decision_reason_codes": ["R06", "R11"],
            "shap_contributions": [
                {"feature": "account_age_months", "value": -0.18, "direction": "negative"},
                {"feature": "num_active_loans", "value": -0.09, "direction": "negative"},
                {"feature": "payment_history_strength", "value": 0.12, "direction": "positive"},
            ],
        },
    },
    {
        "identification": "QIK9845686769",
        "full_name": "Kwame Laryea",
        "date_of_birth": "1971-06-16",
        "phone": "0542376768",
        "label": "Borderline — active arrears, refer to human",
        "outcome": {
            "decision": "REFER",
            "credit_score": 612,
            "score_grade": "C",
            "risk_tier": "MEDIUM",
            "probability_of_default": 0.22,
            "pd_confidence": 0.79,
            "fraud_risk_flag": "LOW",
            "fraud_anomaly_score": 0.12,
            "recommended_amount_ghs": None,
            "recommended_loan_tier": None,
            "income_tier": 2,
            "income_tier_label": "UPPER_MID",
            "income_confidence": 0.77,
            "data_quality_score": 0.85,
            "conditions": [],
            "decision_reason_codes": ["R03", "R09"],
            "shap_contributions": [
                {"feature": "has_active_arrears", "value": -0.20, "direction": "negative"},
                {"feature": "debt_to_income_ratio", "value": -0.13, "direction": "negative"},
                {"feature": "payment_history_strength", "value": 0.10, "direction": "positive"},
            ],
        },
    },
    {
        "identification": "07762758RP",
        "full_name": "Adwoa Bonsu",
        "date_of_birth": "1980-03-14",
        "phone": "0246057543",
        "label": "Adverse — default on file, decline",
        "outcome": {
            "decision": "DECLINE",
            "credit_score": 430,
            "score_grade": "E",
            "risk_tier": "HIGH",
            "probability_of_default": 0.71,
            "pd_confidence": 0.9,
            "fraud_risk_flag": "MEDIUM",
            "fraud_anomaly_score": 0.34,
            "recommended_amount_ghs": None,
            "recommended_loan_tier": None,
            "income_tier": 1,
            "income_tier_label": "LOWER_MID",
            "income_confidence": 0.7,
            "data_quality_score": 0.82,
            "conditions": [],
            "decline_reasons": [
                "Default recorded on an existing facility",
                "Maximum delinquency rating on the bureau report",
                "Active adverse record",
            ],
            "decision_reason_codes": ["R21", "R23", "R30"],
            "shap_contributions": [
                {"feature": "num_delinquencies_24m", "value": -0.31, "direction": "negative"},
                {"feature": "has_judgement", "value": -0.22, "direction": "negative"},
                {"feature": "total_outstanding_debt", "value": -0.15, "direction": "negative"},
            ],
        },
    },
    {
        "identification": "WZN5050578295",
        "full_name": "Emmanuel Asante",
        "date_of_birth": "1982-09-15",
        "phone": "0240003827",
        "label": "Fraud pattern — rapid accumulation, hold",
        "outcome": {
            "decision": "FRAUD_HOLD",
            "credit_score": 520,
            "score_grade": "D",
            "risk_tier": "HIGH",
            "probability_of_default": 0.48,
            "pd_confidence": 0.74,
            "fraud_risk_flag": "HIGH",
            "fraud_anomaly_score": 0.88,
            "recommended_amount_ghs": None,
            "recommended_loan_tier": None,
            "income_tier": 1,
            "income_tier_label": "LOWER_MID",
            "income_confidence": 0.68,
            "data_quality_score": 0.8,
            "conditions": [],
            "decline_reasons": [
                "Rapid multi-account accumulation across lenders",
                "Rising arrears across newly opened facilities (bust-out pattern)",
            ],
            "decision_reason_codes": ["F02", "F07"],
            "shap_contributions": [
                {"feature": "recent_inquiries_6m", "value": -0.29, "direction": "negative"},
                {"feature": "num_active_loans", "value": -0.24, "direction": "negative"},
                {"feature": "mobile_money_velocity", "value": -0.18, "direction": "negative"},
            ],
        },
    },
]

# ── Lookup indexes ──────────────────────────────────────────────────────────
_BY_ID = {r["identification"]: r for r in SIMULATION_RECORDS}
_BY_PHONE = {r["phone"]: r for r in SIMULATION_RECORDS if r.get("phone")}


def _norm(v: str | None) -> str | None:
    return v.strip() if isinstance(v, str) and v.strip() else None


def get_simulation_report(
    identification: str | None, phone: str | None = None
) -> dict | None:
    """Return the raw XDS report for a known demo ID/phone, else None."""
    rec = _BY_ID.get(_norm(identification) or "") or _BY_PHONE.get(_norm(phone) or "")
    if not rec:
        return None
    return _xds_fixtures().get(rec["identification"])


def get_outcome_by_consumer_id(consumer_id: str | None) -> dict | None:
    """Return the deterministic scoring outcome for a demo consumer id.

    consumer_id == the identification (we patch the XDS consumerID to match),
    so the value the frontend echoes back from bureau-lookup resolves here.
    """
    rec = _BY_ID.get(_norm(consumer_id) or "")
    return rec["outcome"] if rec else None


def all_records() -> list[dict[str, Any]]:
    """Cheat-sheet data (no raw XDS): id, name, dob, expected decision."""
    return [
        {
            "identification": r["identification"],
            "full_name": r["full_name"],
            "date_of_birth": r["date_of_birth"],
            "phone": r["phone"],
            "label": r["label"],
            "expected_decision": r["outcome"]["decision"],
        }
        for r in SIMULATION_RECORDS
    ]
