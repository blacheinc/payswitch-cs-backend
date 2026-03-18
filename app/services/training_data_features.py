"""Target feature schema for credit scoring model training.

These are the 18 features the ML model needs. Any source data from banks
must be mapped to these fields (plus the mandatory `approved` target variable).
"""

from __future__ import annotations

from typing import Any


# The 18 features we extract from every data source
TARGET_FEATURES: dict[str, dict[str, Any]] = {
    "age": {
        "type": "integer",
        "required": False,
        "description": "Applicant age in years",
    },
    "monthly_income": {
        "type": "float",
        "required": True,
        "description": "Gross monthly income",
    },
    "employment_years": {
        "type": "float",
        "required": False,
        "description": "Years at current employment",
    },
    "employment_type": {
        "type": "string",
        "required": False,
        "description": "Employment category (salaried, self-employed, contract, etc.)",
    },
    "account_balance": {
        "type": "float",
        "required": True,
        "description": "Current account balance",
    },
    "savings_balance": {
        "type": "float",
        "required": False,
        "description": "Savings account balance",
    },
    "average_monthly_balance": {
        "type": "float",
        "required": False,
        "description": "Average monthly account balance over 6-12 months",
    },
    "account_age_months": {
        "type": "integer",
        "required": False,
        "description": "How long the account has been open",
    },
    "monthly_transactions_count": {
        "type": "integer",
        "required": False,
        "description": "Number of transactions per month",
    },
    "existing_loans_balance": {
        "type": "float",
        "required": False,
        "description": "Total outstanding loan balance",
    },
    "num_existing_loans": {
        "type": "integer",
        "required": False,
        "description": "Number of active loans",
    },
    "monthly_loan_repayment": {
        "type": "float",
        "required": False,
        "description": "Total monthly loan payments",
    },
    "credit_history_months": {
        "type": "integer",
        "required": False,
        "description": "Length of credit history in months",
    },
    "num_credit_inquiries": {
        "type": "integer",
        "required": False,
        "description": "Number of credit inquiries in last 12 months",
    },
    "num_late_payments": {
        "type": "integer",
        "required": False,
        "description": "Number of late payments in history",
    },
    "loan_amount_requested": {
        "type": "float",
        "required": True,
        "description": "Amount of loan being requested",
    },
    "loan_tenure_months": {
        "type": "integer",
        "required": True,
        "description": "Requested loan duration in months",
    },
    "debt_to_income_ratio": {
        "type": "float",
        "required": False,
        "description": "Debt-to-income ratio (auto-calculated if monthly_loan_repayment and monthly_income are present)",
    },
}

# Mandatory target variable — must be present in every dataset
TARGET_VARIABLE = "approved"  # 0 = rejected, 1 = approved

# Feature names as a flat list
TARGET_FEATURE_NAMES: list[str] = list(TARGET_FEATURES.keys())
REQUIRED_FEATURES: list[str] = [k for k, v in TARGET_FEATURES.items() if v["required"]]

# Acceptance thresholds
MIN_FEATURES_MAPPED = 12
MIN_RECORD_COUNT = 500
RECOMMENDED_RECORD_COUNT = 1_000

# Type casting map for transform
TYPE_CAST_MAP = {
    "integer": int,
    "float": float,
    "string": str,
}
