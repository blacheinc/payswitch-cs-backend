"""Target feature schema for credit scoring model training.

These are the 30 features the ML models need, based on XDS Credit Bureau data.
Features are pre-binned/weighted (0-1 scale) by the Data Engineer.
"""

from __future__ import annotations

from typing import Any


# The 30 features extracted from XDS Credit Bureau data (Product 45 + Product 49)
TARGET_FEATURES: dict[str, dict[str, Any]] = {
    # ── Payment History & Delinquency ────────────────────────────────────
    "highest_delinquency_rating": {
        "type": "float",
        "required": False,
        "description": "Worst delinquency rating across all accounts (0=worst, 1=best)",
    },
    "months_on_time_24m": {
        "type": "float",
        "required": False,
        "description": "Proportion of on-time payments in last 24 months",
    },
    "worst_arrears_24m": {
        "type": "float",
        "required": False,
        "description": "Worst arrears status in 24-month payment history",
    },
    "current_streak_on_time": {
        "type": "float",
        "required": False,
        "description": "Length of current on-time payment streak",
    },
    "has_active_arrears": {
        "type": "float",
        "required": False,
        "description": "Whether applicant currently has accounts in arrears (0=yes, 1=no)",
    },
    # ── Debt & Balances ──────────────────────────────────────────────────
    "total_arrear_amount_ghs": {
        "type": "float",
        "required": False,
        "description": "Total arrear amount in GHS (binned 0-1)",
    },
    "total_outstanding_debt_ghs": {
        "type": "float",
        "required": False,
        "description": "Total outstanding debt in GHS (binned 0-1)",
    },
    "utilisation_ratio": {
        "type": "float",
        "required": False,
        "description": "Credit utilisation ratio (balance / limit)",
    },
    # ── Account Portfolio ────────────────────────────────────────────────
    "num_active_accounts": {
        "type": "float",
        "required": False,
        "description": "Number of active credit accounts (binned 0-1)",
    },
    "total_monthly_instalment_ghs": {
        "type": "float",
        "required": False,
        "description": "Total monthly instalment obligation in GHS (binned 0-1)",
    },
    "credit_age_months": {
        "type": "float",
        "required": False,
        "description": "Age of oldest credit account in months (binned 0-1)",
    },
    "num_accounts_total": {
        "type": "float",
        "required": False,
        "description": "Total number of credit accounts ever (binned 0-1)",
    },
    "num_closed_accounts_good": {
        "type": "float",
        "required": False,
        "description": "Number of accounts closed in good standing",
    },
    "product_diversity_score": {
        "type": "float",
        "required": False,
        "description": "Diversity of credit product types",
    },
    # ── Mobile Credit ────────────────────────────────────────────────────
    "mobile_loan_history_count": {
        "type": "float",
        "required": False,
        "description": "Number of mobile loans from Product 49 history",
    },
    "mobile_max_loan_ghs": {
        "type": "float",
        "required": False,
        "description": "Maximum mobile loan amount in GHS (binned 0-1)",
    },
    # ── Adverse Information ──────────────────────────────────────────────
    "has_judgement": {
        "type": "float",
        "required": False,
        "description": "Whether applicant has court judgements (0=no data, 1=no judgements)",
    },
    "has_written_off": {
        "type": "float",
        "required": False,
        "description": "Whether applicant has written-off accounts",
    },
    "has_charged_off": {
        "type": "float",
        "required": False,
        "description": "Whether applicant has charged-off accounts",
    },
    "has_legal_handover": {
        "type": "float",
        "required": False,
        "description": "Whether applicant has accounts handed over to legal",
    },
    "num_bounced_cheques": {
        "type": "float",
        "required": False,
        "description": "Number of dishonoured cheques (binned 0-1)",
    },
    "has_adverse_default": {
        "type": "float",
        "required": False,
        "description": "Whether applicant has adverse/default records",
    },
    # ── Enquiry Activity ─────────────────────────────────────────────────
    "num_enquiries_3m": {
        "type": "float",
        "required": False,
        "description": "Number of credit enquiries in last 3 months (binned 0-1)",
    },
    "num_enquiries_12m": {
        "type": "float",
        "required": False,
        "description": "Number of credit enquiries in last 12 months (binned 0-1)",
    },
    "enquiry_reason_flags": {
        "type": "float",
        "required": False,
        "description": "Encoded enquiry reason pattern",
    },
    # ── Applicant Profile (from application, not XDS) ────────────────────
    "applicant_age": {
        "type": "float",
        "required": False,
        "description": "DE computes from birthDate",
    },
    "identity_verified": {
        "type": "float",
        "required": False,
        "description": "1.0 if KYC verified, 0.0 if not",
    },
    "num_dependants": {
        "type": "float",
        "required": False,
        "description": "Number of dependants (from application)",
    },
    "has_employer_detail": {
        "type": "float",
        "required": False,
        "description": "1.0 if employer detail present, 0.60 if not",
    },
    "address_stability": {
        "type": "float",
        "required": False,
        "description": "1.0/0.75/0.40 based on address count",
    },
}

# ── Target Variables (training labels) ──────────────────────────────────────

TARGET_VARIABLE = "default_flag"  # 0/1 — Credit Risk target
TARGET_COLUMNS = {
    "default_flag": {"type": "integer", "description": "Credit Risk target (0/1)"},
    "max_successful_loan_ghs": {
        "type": "float",
        "description": "Loan Amount target (nullable)",
    },
    "income_tier": {
        "type": "integer",
        "description": "Income Verification target (0-3, nullable)",
    },
}

# ── Metadata Columns (pass-through, not model features) ────────────────────

METADATA_COLUMNS = [
    "request_id",
    "credit_score",
    "score_grade",
    "decision_label",
    "data_quality_score",
    "product_source",
    "bureau_hit_status",
    "applicant_age_at_application",
    "credit_age_months_at_application",
]

# Feature names as a flat list
TARGET_FEATURE_NAMES: list[str] = list(TARGET_FEATURES.keys())
REQUIRED_FEATURES: list[str] = [k for k, v in TARGET_FEATURES.items() if v["required"]]

# Acceptance thresholds
MIN_FEATURES_MAPPED = 20
MIN_RECORD_COUNT = 500
RECOMMENDED_RECORD_COUNT = 5_000

# Total expected columns = 30 features + 3 targets + 9 metadata = 42
TOTAL_PARQUET_COLUMNS = 42

# Type casting map for transform
TYPE_CAST_MAP = {
    "integer": int,
    "float": float,
    "string": str,
}
