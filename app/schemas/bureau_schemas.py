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
    """All 30 features as defined in the DE contract.

    Types are matched to the XDS source data so values roundtrip cleanly
    through the reverse mapper:
      • str  – direct passthrough from a single XDS field (same format)
      • int  – computed counts, binary flags, derived values
      • float – computed ratios
    See docs/de_feature_mapping.md for the full XDS → feature mapping.
    """

    # ── Direct from XDS fields (str passthrough for reverse mapping) ─────
    highest_delinquency_rating: str | None = None           # 45:highestDelinquencyRating.highestDelinquencyRating → "1" / "Good"
    has_active_arrears: str | None = None                   # 45:creditAccountSummary.totalAccountInArrearGHS → "2"
    total_arrear_amount_ghs: str | None = None              # 45:creditAccountSummary.totalAmountInArrearGHS → "1,525.00"
    total_outstanding_debt_ghs: str | None = None           # 45:creditAccountSummary.totalOutstandingdebtGHS → "3,890.00"
    num_active_accounts: str | None = None                  # 45:creditAccountSummary.totalActiveAccountsGHS → "2"
    total_monthly_instalment_ghs: str | None = None         # 45+49 creditAccountSummary instalment → "0.00"
    num_accounts_total: str | None = None                   # 45:creditAccountSummary.totalNumberofAccountsGHS → "3"
    num_bounced_cheques: str | None = None                  # 45:creditAccountSummary.totalNumberofDishonouredGHS → "0"
    mobile_max_loan_ghs: str | None = None                  # 49:creditAccountSummary.highestAmountTaken → "153.00"
    worst_arrears_24m: str | None = None                    # max of creditAgreementSummary[].monthsInArrears → "14"
    num_dependants: str | None = None                       # 45:personalDetailsSummary.dependants → "0"
    identity_verified: str | None = None                    # 45:personalDetailsSummary.nationalIDNo → "" or "RJB7045308753"
    has_employer_detail: str | None = None                   # 45:personalDetailsSummary.employerDetail → "MOE" or ""

    # ── Renamed from XDS fields (aggregated from arrays, still str) ──────
    applicant_age: str | None = None                        # 45:personalDetailsSummary.birthDate → "15/05/1990"
    credit_age_months: str | None = None                    # oldest 45:creditAgreementSummary[].dateAccountOpened → "02/02/2018"
    months_on_time_24m: str | None = None                   # count of "0" in m01–m24 → "18"
    current_streak_on_time: str | None = None               # consecutive on-time from m01 → "0"
    utilisation_ratio: str | None = None                    # currentBalance / openingBalance → "0.3402"
    num_closed_accounts_good: str | None = None             # count: closedDate + non-adverse status → "6"
    product_diversity_score: str | None = None              # count of unique account types → "5"
    mobile_loan_history_count: str | None = None            # len(49:detailedFacilityInfo) → "3"
    has_judgement: str | None = None                         # 1 if judgementSummary non-empty → "0" / "1"
    has_written_off: bool | None = None                     # True if any accountStatusCode == "W"
    has_charged_off: bool | None = None                     # True if any accountStatusCode == "G"
    has_legal_handover: bool | None = None                  # True if any accountStatusCode == "L"
    has_adverse_default: bool | None = None                 # True if defaults/adverseDetails non-empty
    num_enquiries_3m: str | None = None                     # count within last 90 days → "2"
    num_enquiries_12m: str | None = None                    # count within last 365 days → "3"
    enquiry_reason_flags: str | None = None                 # 1 if any enquiries exist → "0" / "1"
    address_stability: str | None = None                    # count of unique addresses → "5"


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
    applicant_age_at_application: str | None = None
    credit_age_months_at_application: str | None = None


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
    highest_delinquency_rating=None,
    has_active_arrears="0",
    total_arrear_amount_ghs="0.00",
    total_outstanding_debt_ghs="0.00",
    num_active_accounts="0",
    total_monthly_instalment_ghs="0.00",
    num_accounts_total="0",
    num_bounced_cheques="0",
    mobile_max_loan_ghs=None,
    worst_arrears_24m=None,
    num_dependants=None,
    identity_verified=None,
    has_employer_detail=None,
    applicant_age=None,
    credit_age_months=None,
    months_on_time_24m=None,
    current_streak_on_time=None,
    utilisation_ratio=None,
    num_closed_accounts_good="0",
    product_diversity_score="0",
    mobile_loan_history_count=None,
    has_judgement="0",
    has_written_off=False,
    has_charged_off=False,
    has_legal_handover=False,
    has_adverse_default=False,
    num_enquiries_3m="0",
    num_enquiries_12m="0",
    enquiry_reason_flags=None,
    address_stability=None,
)
