"""Score request schemas – full PRD-compliant input/output schemas."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.bureau_schemas import (
    BureauCreditAccount,
    BureauCreditSummary,
    BureauFeatures,
)
from app.schemas.common import BaseSchema


# ── Score Request Input (mirrors PRD Section 7.1) ─────────────────────────────


class ApplicantAddress(BaseModel):
    street: str | None = None
    city: str | None = None
    region: str | None = None
    digital_address: str | None = None


class ApplicantInfo(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    date_of_birth: date
    national_id_type: str = Field(
        description="ghana_card, voter_id, passport, drivers_license, nhis"
    )
    national_id_number: str = Field(max_length=50)
    gender: str | None = Field(default=None, description="male, female, other")
    marital_status: str | None = None
    phone: str = Field(pattern=r"^\+?[0-9]{10,15}$")
    email: str | None = None
    address: ApplicantAddress | None = None
    dependents: int | None = Field(default=None, ge=0, le=20)
    education_level: str | None = None


class EmploymentInfo(BaseModel):
    status: str | None = Field(
        default=None,
        description="employed, self_employed, unemployed, retired, student",
    )
    employer_name: str | None = None
    employer_industry: str | None = None
    job_title: str | None = None
    duration_months: int | None = Field(default=None, ge=0)
    total_work_experience_months: int | None = Field(default=None, ge=0)
    monthly_income: float | None = Field(default=None, ge=0)
    income_currency: str = "GHS"
    income_verification_type: str | None = None
    other_income: float | None = Field(default=None, ge=0)
    other_income_source: str | None = None


class LoanRequestInfo(BaseModel):
    amount: float = Field(gt=0)
    currency: str = "GHS"
    purpose: str = Field(
        description="personal, business, education, housing, vehicle, medical, agriculture, debt_consolidation, other"
    )
    purpose_description: str | None = Field(default=None, max_length=500)
    tenure_months: int = Field(ge=1, le=360)
    collateral_type: str | None = None
    collateral_value: float | None = Field(default=None, ge=0)


class ExistingLoan(BaseModel):
    lender_name: str | None = None
    loan_type: str | None = None
    original_amount: float | None = None
    outstanding_balance: float | None = None
    monthly_payment: float | None = None
    status: str | None = None
    days_past_due: int | None = None


class BankAccount(BaseModel):
    bank_name: str | None = None
    account_type: str | None = None
    average_balance_3m: float | None = None
    account_age_months: int | None = None


class MobileMoneyInfo(BaseModel):
    active: bool = False
    providers: list[str] | None = None
    avg_monthly_inflow: float | None = None
    avg_monthly_outflow: float | None = None
    account_age_months: int | None = None


class AssetsInfo(BaseModel):
    owns_property: bool | None = None
    property_value: float | None = None
    owns_vehicle: bool | None = None
    vehicle_value: float | None = None
    savings_investments: float | None = None


class FinancialProfile(BaseModel):
    existing_loans: list[ExistingLoan] | None = None
    has_bank_account: bool | None = None
    bank_accounts: list[BankAccount] | None = None
    mobile_money: MobileMoneyInfo | None = None
    has_credit_card: bool | None = None
    credit_card_utilization: float | None = Field(default=None, ge=0, le=1)
    monthly_expenses: float | None = None
    assets: AssetsInfo | None = None


class BureauData(BaseModel):
    """Bureau data from the bureau-lookup step, forwarded in the score request.

    The frontend receives structured data from ``POST /v1/bureau-lookup``
    (personal details, credit summary, accounts, features) and renders
    it in a form.  The user reviews and may modify values.  When they
    submit the score request, the (possibly modified) data comes back
    here so the backend can:
      1. Retrieve cached raw XDS from Redis via ``consumer_id``
      2. Patch the cached XDS with any user modifications
      3. Send the patched XDS to the Data Engineer in the same format
    """

    # Key for retrieving cached raw XDS data from Redis
    consumer_id: str | None = None

    # Bureau hit metadata
    bureau_hit_status: str | None = Field(
        default=None, description="HIT, NO_RECORD, or MULTIPLE_MATCH"
    )
    product_source: str | None = Field(
        default=None, description="45, 49, or 45+49"
    )

    # Credit summary — maps to creditAccountSummary in XDS
    credit_summary: BureauCreditSummary | None = None

    # Individual credit accounts — maps to creditAgreementSummary (P45)
    # and detailedFacilityInfo (P49)
    credit_accounts: list[BureauCreditAccount] | None = None

    # Pre-binned features returned by bureau-lookup (for audit / display)
    features: BureauFeatures | None = None


class TelcoData(BaseModel):
    account_age_months: int | None = None
    avg_monthly_spend: float | None = None
    payment_regularity: str | None = None


class SocialSignals(BaseModel):
    linkedin_profile: bool | None = None
    professional_associations: int | None = None


class AlternativeData(BaseModel):
    utility_payment_history: str | None = None
    rent_payment_history: str | None = None
    telco_data: TelcoData | None = None
    social_signals: SocialSignals | None = None


class ConsentInfo(BaseModel):
    bureau_check_authorized: bool = True
    consent_date: date
    consent_reference: str | None = None
    data_sharing_authorized: bool | None = None


class RequestMetadata(BaseModel):
    channel: str | None = None
    product_type: str | None = None
    branch_code: str | None = None
    officer_id: str | None = None
    callback_url: str | None = None


class CreateScoreRequestInput(BaseModel):
    """Full credit score request – matches PRD Section 7.1."""

    reference_id: str | None = Field(default=None, max_length=100)
    applicant: ApplicantInfo
    employment: EmploymentInfo | None = None
    loan_request: LoanRequestInfo
    financial_profile: FinancialProfile | None = None
    bureau_data: BureauData | None = None
    alternative_data: AlternativeData | None = None
    consent: ConsentInfo
    metadata: RequestMetadata | None = None


# ── Score Response (mirrors PRD Section 7.2) ──────────────────────────────────


class ScoreValue(BaseModel):
    value: int = Field(ge=300, le=850)
    max_value: int = 850
    min_value: int = 300
    percentile: int | None = Field(default=None, ge=1, le=100)
    risk_category: str
    risk_category_description: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    data_quality_flag: str | None = None


class ScoreComponent(BaseModel):
    score: int
    max_score: int
    weight: float | None = None
    grade: str | None = None


class ScoreComponents(BaseModel):
    payment_history: ScoreComponent | None = None
    credit_utilization: ScoreComponent | None = None
    credit_history_length: ScoreComponent | None = None
    credit_mix: ScoreComponent | None = None
    new_credit: ScoreComponent | None = None
    income_stability: ScoreComponent | None = None
    debt_burden: ScoreComponent | None = None


class RiskFactor(BaseModel):
    code: str
    category: str
    description: str
    impact: str
    severity: str
    detail: str | None = None


class Recommendation(BaseModel):
    type: str
    message: str
    priority: str


class AffordabilityAssessment(BaseModel):
    estimated_monthly_payment: float | None = None
    debt_to_income_current: float | None = None
    debt_to_income_projected: float | None = None
    disposable_income: float | None = None
    affordability_assessment: str | None = None
    max_recommended_amount: float | None = None


class ModelInfo(BaseModel):
    model_version: str | None = None
    model_type: str | None = None
    features_used: int | None = None
    data_sources: list[str] | None = None


class ScoreTimestamps(BaseModel):
    requested_at: datetime
    scored_at: datetime | None = None
    valid_until: datetime | None = None


class ScoreError(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ScoreRequestResponse(BaseModel):
    """Full score response – matches PRD Section 7.2."""

    request_id: str
    reference_id: str | None = None
    status: str
    score: ScoreValue | None = None
    score_components: ScoreComponents | None = None
    risk_factors: list[RiskFactor] | None = None
    recommendations: list[Recommendation] | None = None
    affordability: AffordabilityAssessment | None = None
    model_info: ModelInfo | None = None
    timestamps: ScoreTimestamps
    error: ScoreError | None = None


# ── Score Request List Item (lighter response) ────────────────────────────────


class ScoreRequestSummary(BaseSchema):
    request_id: str
    tracking_id: str
    reference_id: str | None
    status: str
    score_value: int | None
    risk_category: str | None
    request_source: str
    created_at: datetime
    scored_at: datetime | None


# ── Outcome / Performance ────────────────────────────────────────────────────


class RecordOutcomeRequest(BaseModel):
    decision: str = Field(description="approved, declined, referred")
    approved_amount: float | None = None
    approved_tenure_months: int | None = None
    interest_rate: float | None = None
    decline_reason: str | None = None
    decision_notes: str | None = None
    decision_date: date | None = None


class RecordPerformanceRequest(BaseModel):
    status: str = Field(
        description="current, past_due_30, past_due_60, past_due_90, default, paid_off, write_off"
    )
    days_past_due: int | None = None
    outstanding_balance: float | None = None
    as_of_date: date


class OutcomeResponse(BaseModel):
    message: str = "Decision recorded successfully"
    request_id: str
    decision: str


class PerformanceResponse(BaseModel):
    message: str = "Performance data recorded"
    request_id: str


# ── Scoring Result (4-model AI pipeline output) ──────────────────────────────


class ShapContribution(BaseModel):
    """A single SHAP feature contribution from the AI pipeline."""

    feature: str
    value: float
    direction: str  # "positive" or "negative"


class CreditRiskResult(BaseModel):
    """Credit risk model output from the AI pipeline."""

    probability_of_default: float | None = Field(default=None, ge=0, le=1)
    pd_confidence: float | None = Field(default=None, ge=0, le=1)
    risk_tier: str | None = None  # LOW / MEDIUM / HIGH
    shap_contributions: list[ShapContribution] | None = None
    decision_reason_codes: list[str] | None = None  # R01, R06, etc.
    model_version: str | None = None


class FraudDetectionResult(BaseModel):
    """Fraud detection model output from the AI pipeline."""

    fraud_anomaly_score: float | None = Field(default=None, ge=0, le=1)
    fraud_risk_flag: str | None = None  # LOW / MEDIUM / HIGH
    model_version: str | None = None


class LoanAmountResult(BaseModel):
    """Loan amount model output from the AI pipeline (Phase 2, nullable)."""

    recommended_amount_ghs: float | None = None
    recommended_loan_tier: str | None = None  # SMALL / MEDIUM / LARGE
    model_version: str | None = None


class IncomeVerificationResult(BaseModel):
    """Income verification model output from the AI pipeline (Phase 2, nullable)."""

    income_tier: int | None = Field(default=None, ge=0, le=3)
    income_tier_label: str | None = None  # LOWER / LOWER_MID / UPPER_MID / UPPER
    income_confidence: float | None = Field(default=None, ge=0, le=1)
    model_version: str | None = None


class ScoringMetadata(BaseModel):
    """Metadata passed through from DE via AI pipeline."""

    credit_score: int | None = None
    score_grade: str | None = None  # A-F
    data_quality_score: float | None = None
    bureau_hit_status: str | None = None  # HIT / NO_RECORD
    product_source: str | None = None  # "45+49" / "45" / "49"
    applicant_age_at_application: int | None = None
    credit_age_months_at_application: int | None = None


class ScoringResultResponse(BaseModel):
    """Full scoring result from the AI pipeline — shown to loan officers."""

    request_id: str
    scoring_timestamp: datetime | None = None
    decision: str  # APPROVE / CONDITIONAL_APPROVE / REFER / DECLINE / FRAUD_HOLD / ERROR
    condition_applied: list[str] | None = None  # CA-01 through CA-06

    credit_risk: CreditRiskResult | None = None
    fraud_detection: FraudDetectionResult | None = None
    loan_amount: LoanAmountResult | None = None
    income_verification: IncomeVerificationResult | None = None

    scoring_metadata: ScoringMetadata | dict[str, Any] | None = None
    errors: list[str] | None = None  # Only for ERROR decisions


class HardStopDeclineResponse(BaseModel):
    """Response when an applicant is immediately declined by hard-stop rules."""

    request_id: str
    status: str = "DECLINED"
    decision: str = "DECLINE"
    decline_reasons: list[str]
    triggered_rules: list[str]


class OverrideDecisionRequest(BaseModel):
    """Manual decision override for REFER cases."""

    decision: str = Field(
        description="APPROVE, CONDITIONAL_APPROVE, or DECLINE"
    )
    override_reason: str = Field(min_length=5, max_length=1000)
    conditions: list[str] | None = None
    approved_amount: float | None = None
    approved_tenure_months: int | None = None


class OverrideDecisionResponse(BaseModel):
    """Response after manual decision override."""

    message: str = "Decision overridden successfully"
    request_id: str
    previous_decision: str
    new_decision: str
    overridden_by: str | None = None
