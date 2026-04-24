"""Score request schemas – full PRD-compliant input/output schemas."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.bureau_schemas import (
    BureauCreditAccount,
    BureauCreditSummary,
    BureauFeatures,
)
from app.schemas.common import BaseSchema


# ── Score Request Input (mirrors PRD Section 7.1) ─────────────────────────────


class ApplicantInfo(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    date_of_birth: date
    national_id_number: str | None = Field(default=None, max_length=50)
    phone: str | None = None
    account_number: str | None = Field(default=None, max_length=50)


class LoanRequestInfo(BaseModel):
    amount: float = Field(gt=0)
    tenure_months: int = Field(ge=1, le=360)
    purpose: str | None = None


# ── Response summaries (echo back what was submitted) ──────────────────────────


class ApplicantSummary(BaseModel):
    """Lightweight applicant info returned in score responses."""

    full_name: str | None = None
    date_of_birth: str | None = None
    national_id_number: str | None = None
    phone: str | None = None
    account_number: str | None = None


class LoanRequestSummary(BaseModel):
    """Lightweight loan request info returned in score responses."""

    amount: float | None = None
    tenure_months: int | None = None
    purpose: str | None = None


class BureauData(BaseModel):
    """Bureau data from the bureau-lookup step, forwarded in the score request."""
    # Key for retrieving cached raw XDS data from Redis
    consumer_id: str
    # Bureau hit metadata
    bureau_hit_status: str | None = None
    product_source: str | None = None
    # Credit summary — maps to creditAccountSummary in XDS
    credit_summary: BureauCreditSummary | None = None
    # Individual credit accounts
    credit_accounts: list[BureauCreditAccount] | None = None
    # Pre-binned features returned by bureau-lookup (for audit / display)
    features: BureauFeatures | None = None


class RequestMetadata(BaseModel):
    channel: str | None = None
    callback_url: str | None = None


class CreateScoreRequestInput(BaseModel):
    """Minimal credit score request – only expects exactly what the backend needs."""

    reference_id: str | None = Field(default=None, max_length=100)
    applicant: ApplicantInfo
    loan_request: LoanRequestInfo
    bureau_data: BureauData
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

    message: str = "Score request submitted successfully. Your request is being processed."
    request_id: str
    reference_id: str | None = None
    status: str
    applicant: ApplicantSummary | None = None
    loan_request: LoanRequestSummary | None = None
    score: ScoreValue | None = None
    score_components: ScoreComponents | None = None
    risk_factors: list[RiskFactor] | None = None
    recommendations: list[Recommendation] | None = None
    affordability: AffordabilityAssessment | None = None
    model_info: ModelInfo | None = None
    timestamps: ScoreTimestamps
    scoring_result: Optional["ScoringResultResponse"] = None
    error: ScoreError | None = None


# ── Score Request List Item (lighter response) ────────────────────────────────


class ScoreRequestSummary(BaseSchema):
    request_id: str
    tracking_id: str
    reference_id: str | None
    status: str
    applicant_name: str | None = None
    loan_amount: float | None = None
    loan_purpose: str | None = None
    score_value: int | None
    risk_category: str | None
    request_source: str
    created_at: datetime
    scored_at: datetime | None
    scoring_result: Optional["ScoringResultResponse"] = None


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
    """Metadata passed through from DE via AI pipeline.

    ``extra = "allow"`` is critical: hard-stop declines store additional
    keys (``hard_stop``, ``decline_reasons``, ``triggered_rules``) that
    must survive Pydantic parsing so the model_validator on
    ScoringResultResponse can extract them.
    """

    model_config = {"extra": "allow"}

    credit_score: int | None = None
    score_grade: str | None = None  # A-F
    data_quality_score: float | None = None
    bureau_hit_status: str | None = None  # HIT / NO_RECORD
    product_source: str | None = None  # "45+49" / "45" / "49"
    applicant_age_at_application: str | None = None
    credit_age_months_at_application: str | None = None


class ScoringResultResponse(BaseModel):
    """Full scoring result from the AI pipeline — shown to loan officers."""
    
    model_config = {"from_attributes": True}

    request_id: str
    scoring_timestamp: datetime | None = None
    decision: str  # APPROVE / CONDITIONAL_APPROVE / REFER / DECLINE / FRAUD_HOLD / ERROR
    condition_applied: list[str] | None = None  # CA-01 through CA-06

    # Hard-stop decline details (populated when decision is a hard-stop DECLINE)
    decline_reasons: list[str] | None = None
    triggered_rules: list[str] | None = None

    credit_risk: CreditRiskResult | None = None
    fraud_detection: FraudDetectionResult | None = None
    loan_amount: LoanAmountResult | None = None
    income_verification: IncomeVerificationResult | None = None

    scoring_metadata: ScoringMetadata | dict[str, Any] | None = None
    errors: list[str] | None = None  # Only for ERROR decisions

    @model_validator(mode="after")
    def _surface_hard_stop_details(self) -> "ScoringResultResponse":
        """Extract decline reasons from scoring_metadata for hard-stop declines.

        When loaded via ``from_attributes`` (ORM relationship), the ORM object
        has no ``decline_reasons`` column — the data lives inside the
        ``scoring_metadata`` JSONB.  This validator ensures those fields are
        always surfaced as first-class response fields.
        """
        meta = self.scoring_metadata
        # Handle both dict and ScoringMetadata (which uses extra="allow")
        if meta is None:
            return self

        if isinstance(meta, ScoringMetadata):
            is_hard_stop = getattr(meta, "hard_stop", False)
            if is_hard_stop:
                if not self.decline_reasons:
                    self.decline_reasons = getattr(meta, "decline_reasons", None)
                if not self.triggered_rules:
                    self.triggered_rules = getattr(meta, "triggered_rules", None)
        elif isinstance(meta, dict) and meta.get("hard_stop"):
            if not self.decline_reasons:
                self.decline_reasons = meta.get("decline_reasons")
            if not self.triggered_rules:
                self.triggered_rules = meta.get("triggered_rules")
        return self


class HardStopDeclineResponse(BaseModel):
    """Response when an applicant is immediately declined by hard-stop rules."""

    message: str = "Application declined — eligibility criteria not met."
    request_id: str
    status: str = "DECLINED"
    decision: str = "DECLINE"
    applicant: ApplicantSummary | None = None
    loan_request: LoanRequestSummary | None = None
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


# ── Dashboard stats (GET /v1/score-requests/stats) ────────────────────────────


class ScoreDistributionBucket(BaseModel):
    """One row of the FICO-style score histogram."""

    range: str
    count: int


class StatsDecisionCounts(BaseModel):
    """Decision mix for the current window.

    Keys are the authoritative ``ScoringDecision`` enum values. ``ERROR``
    covers pipeline failures and is rendered as "Could not score" on the FE.
    """

    APPROVE: int = 0
    CONDITIONAL_APPROVE: int = 0
    DECLINE: int = 0
    REFER: int = 0
    ERROR: int = 0
    FRAUD_HOLD: int = 0


class StatsCurrent(BaseModel):
    """Aggregates over the current window."""

    total_requests: int
    decided: int
    avg_credit_score: int | None = None
    median_credit_score: int | None = None
    approval_rate_pct: float
    decision_counts: StatsDecisionCounts
    score_distribution: list[ScoreDistributionBucket]


class StatsPrevious(BaseModel):
    """Aggregates over the immediately-prior window of the same length.

    Empty when the org has no history in that range — the FE uses ``null``
    trend fields as a signal to suppress trend-pill rendering.
    """

    total_requests: int
    decided: int
    avg_credit_score: int | None = None
    approval_rate_pct: float


class StatsTrend(BaseModel):
    """Pre-computed deltas.

    Every field is ``None`` when the previous window had no activity —
    deltas against an empty baseline are misleading and the FE hides the
    pill entirely rather than showing a bogus "↑ 0%".
    """

    total_delta_pct: float | None = None
    approval_delta_pp: float | None = None
    score_delta: int | None = None


class StatsNeedsAttention(BaseModel):
    """Counts powering the action-queue card.

    ``pending_or_processing`` is an instantaneous count (not period-scoped)
    because the card's job is to surface in-flight work right now.
    ``referred`` and ``failed`` are scoped to the current window.
    """

    referred: int = 0
    pending_or_processing: int = 0
    failed: int = 0


class ScoreRequestStatsResponse(BaseModel):
    """Response body for ``GET /v1/score-requests/stats``."""

    period: str
    generated_at: datetime
    current: StatsCurrent
    previous: StatsPrevious
    trend: StatsTrend
    needs_attention: StatsNeedsAttention
