"""Pydantic schemas for the XDS Credit Bureau lookup and feature extraction."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field


# ── Bureau Lookup Request ───────────────────────────────────────────────────


class BureauLookupRequest(BaseModel):
    """Customer identifiers to look up in XDS Credit Bureau."""

    full_name: str | None = Field(
        default=None, min_length=2, max_length=200,
        description="Consumer name. Optional if identification is provided.",
    )
    date_of_birth: date
    identification: str | None = Field(
        default=None, max_length=50, description="National ID / Ghana Card number"
    )
    phone_number: str | None = Field(
        default=None, pattern=r"^\+?[0-9]{10,15}$", description="For mobile match"
    )
    account_number: str | None = Field(default=None, max_length=50)
    enquiry_reason: str = Field(
        default="Application for credit by a borrower",
        description="Reason for the credit enquiry",
    )


# ── Bureau Features (the 30 XDS bin-weighted features) ──────────────────────


class BureauFeatures(BaseModel):
    """All 30 pre-binned features (0-1 scale) as defined in the contract."""

    highest_delinquency_rating: float | None = Field(default=None, ge=0, le=1)
    months_on_time_24m: float | None = Field(default=None, ge=0, le=1)
    worst_arrears_24m: float | None = Field(default=None, ge=0, le=1)
    current_streak_on_time: float | None = Field(default=None, ge=0, le=1)
    has_active_arrears: float | None = Field(default=None, ge=0, le=1)
    total_arrear_amount_ghs: float | None = Field(default=None, ge=0, le=1)
    total_outstanding_debt_ghs: float | None = Field(default=None, ge=0, le=1)
    utilisation_ratio: float | None = Field(default=None, ge=0, le=1)
    num_active_accounts: float | None = Field(default=None, ge=0, le=1)
    total_monthly_instalment_ghs: float | None = Field(default=None, ge=0, le=1)
    credit_age_months: float | None = Field(default=None, ge=0, le=1)
    num_accounts_total: float | None = Field(default=None, ge=0, le=1)
    num_closed_accounts_good: float | None = Field(default=None, ge=0, le=1)
    product_diversity_score: float | None = Field(default=None, ge=0, le=1)
    mobile_loan_history_count: float | None = Field(default=None, ge=0, le=1)
    mobile_max_loan_ghs: float | None = Field(default=None, ge=0, le=1)
    has_judgement: float | None = Field(default=None, ge=0, le=1)
    has_written_off: float | None = Field(default=None, ge=0, le=1)
    has_charged_off: float | None = Field(default=None, ge=0, le=1)
    has_legal_handover: float | None = Field(default=None, ge=0, le=1)
    num_bounced_cheques: float | None = Field(default=None, ge=0, le=1)
    has_adverse_default: float | None = Field(default=None, ge=0, le=1)
    num_enquiries_3m: float | None = Field(default=None, ge=0, le=1)
    num_enquiries_12m: float | None = Field(default=None, ge=0, le=1)
    enquiry_reason_flags: float | None = Field(default=None, ge=0, le=1)
    applicant_age: float | None = Field(default=None, ge=0, le=1)
    identity_verified: float | None = Field(default=None, ge=0, le=1)
    num_dependants: float | None = Field(default=None, ge=0, le=1)
    has_employer_detail: float | None = Field(default=None, ge=0, le=1)
    address_stability: float | None = Field(default=None, ge=0, le=1)


# ── Bureau Metadata ─────────────────────────────────────────────────────────


class BureauMetadata(BaseModel):
    """Computed metadata from the bureau report."""

    credit_score: int | None = None
    score_grade: str | None = Field(
        default=None, pattern=r"^[A-F]$", description="A-F grade"
    )
    decision_label: str | None = Field(
        default=None,
        description="APPROVE / CONDITIONAL_APPROVE / REFER / DECLINE",
    )
    data_quality_score: float | None = Field(default=None, ge=0, le=1)
    bureau_hit_status: str = Field(
        default="HIT", description="HIT or NO_RECORD"
    )
    product_source: str | None = Field(
        default=None, description="45, 49, 45+49"
    )
    applicant_age_at_application: int | None = None
    credit_age_months_at_application: int | None = None


# ── Bureau Lookup Response ──────────────────────────────────────────────────


class BureauPersonalDetails(BaseModel):
    """Personal details from the XDS report, displayed to the user."""

    consumer_id: str | None = None
    first_name: str | None = None
    surname: str | None = None
    other_names: str | None = None
    birth_date: str | None = None
    gender: str | None = None
    national_id: str | None = None
    phone: str | None = None
    email: str | None = None
    employer: str | None = None
    address: str | None = None
    marital_status: str | None = None
    dependants: str | None = None


class BureauCreditSummary(BaseModel):
    """Credit account summary from XDS."""

    total_accounts: int | None = None
    total_monthly_instalment_ghs: float | None = None
    total_outstanding_debt_ghs: float | None = None
    total_accounts_in_arrear: int | None = None
    total_arrear_amount_ghs: float | None = None
    total_accounts_good_standing: int | None = None
    total_judgements: int | None = None
    total_judgement_amount_ghs: float | None = None
    total_dishonoured_cheques: int | None = None
    delinquency_rating: str | None = None


class BureauCreditAccount(BaseModel):
    """Individual credit account from the bureau report."""

    subscriber_name: str | None = None
    account_no: str | None = None
    date_opened: str | None = None
    facility_type: str | None = None
    currency: str | None = None
    credit_limit: float | None = None
    current_balance: float | None = None
    instalment_amount: float | None = None
    arrear_amount: float | None = None
    months_in_arrears: int | None = None
    status_code: str | None = None
    payment_history_24m: list[str] | None = None


class BureauLookupResponse(BaseModel):
    """Full response from the bureau lookup endpoint."""

    # Status
    bureau_hit_status: str = Field(description="HIT, NO_RECORD, or MULTIPLE_MATCH")
    match_count: int = 0

    # Personal details (only on HIT)
    personal_details: BureauPersonalDetails | None = None

    # Credit summary (only on HIT)
    credit_summary: BureauCreditSummary | None = None

    # Individual credit accounts (only on HIT)
    credit_accounts: list[BureauCreditAccount] | None = None

    # Pre-computed features (30 bin-weighted values, 0-1 scale)
    features: BureauFeatures | None = None

    # Metadata
    metadata: BureauMetadata | None = None

    # Enquiry reference (needed for subsequent API calls or audit)
    enquiry_id: str | None = None
    consumer_id: str | None = None

    # Raw data for debugging (only in development)
    raw_product_45: dict[str, Any] | None = Field(default=None, exclude=True)
    raw_product_49: dict[str, Any] | None = Field(default=None, exclude=True)


# ── Default Feature Values for NO_RECORD ────────────────────────────────────

NO_RECORD_DEFAULT_FEATURES = BureauFeatures(
    highest_delinquency_rating=0.00,
    months_on_time_24m=0.00,
    worst_arrears_24m=0.00,
    current_streak_on_time=0.20,
    has_active_arrears=0.00,
    total_arrear_amount_ghs=1.00,
    total_outstanding_debt_ghs=1.00,
    utilisation_ratio=1.00,
    num_active_accounts=0.15,
    total_monthly_instalment_ghs=1.00,
    credit_age_months=0.15,
    num_accounts_total=0.00,
    num_closed_accounts_good=0.10,
    product_diversity_score=0.30,
    mobile_loan_history_count=0.20,
    mobile_max_loan_ghs=0.00,
    has_judgement=0.00,
    has_written_off=0.00,
    has_charged_off=0.00,
    has_legal_handover=0.00,
    num_bounced_cheques=0.00,
    has_adverse_default=0.00,
    num_enquiries_3m=1.00,
    num_enquiries_12m=1.00,
    enquiry_reason_flags=0.00,
    # These 5 come from the application, not XDS
    applicant_age=None,
    identity_verified=None,
    num_dependants=None,
    has_employer_detail=None,
    address_stability=None,
)
