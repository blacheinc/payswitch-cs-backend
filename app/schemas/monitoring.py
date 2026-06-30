"""Pydantic schemas for monitoring and dashboard API endpoints.

Covers:
- GET /v1/models/current
- POST /v1/rules/evaluate
- GET /v1/monitoring/infrastructure
- GET /v1/monitoring/risk
- GET /v1/monitoring/model-ops
- GET /v1/monitoring/compliance
- GET /v1/monitoring/alerts
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ── Shared ────────────────────────────────────────────────────────────────────


class AlertItem(BaseModel):
    """Inline alert status for dashboard responses."""

    metric: str
    current_value: float
    threshold: float
    status: str  # "ok", "firing", "resolved"
    # Optional context fields
    feature: str | None = None
    model_type: str | None = None


# ── GET /v1/models/current ────────────────────────────────────────────────────


class ChampionModelEntry(BaseModel):
    """Single model entry in the champion metadata blob."""

    model_type: str
    registry_name: str
    version: str
    status: str
    created_at: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    tags: dict[str, str] | None = None
    # Passed through as-is for NOT_REGISTERED models
    error: str | None = None


class ChampionModelResponse(BaseModel):
    """Response for GET /v1/models/current."""

    updated_at: str
    models: list[ChampionModelEntry]


# ── POST /v1/rules/evaluate ──────────────────────────────────────────────────


class RuleEvaluateRequest(BaseModel):
    """Request body for sandbox rule evaluation."""

    probability_of_default: float = Field(
        ..., ge=0.0, le=1.0, description="PD value between 0 and 1"
    )
    score_grade: str = Field(..., pattern="^[A-F]$", description="Score grade A through F")
    data_engineer_decision_label: str | None = None
    fraud_risk_flag: str | None = None
    recommended_loan_amount_ghs: float | None = None
    features: dict[str, float] | None = None
    metadata: dict[str, Any] | None = None


# ── GET /v1/monitoring/infrastructure ─────────────────────────────────────────


class EndpointVolume(BaseModel):
    endpoint: str
    method: str
    count: int


class RequestVolume(BaseModel):
    total: int
    by_endpoint: list[EndpointVolume]


class EndpointLatency(BaseModel):
    endpoint: str
    p50_ms: float
    p95_ms: float
    p99_ms: float


class Latency(BaseModel):
    p50_ms: float
    p95_ms: float
    p99_ms: float
    by_endpoint: list[EndpointLatency]


class StatusError(BaseModel):
    status_code: int
    count: int
    pct: float


class ErrorRates(BaseModel):
    overall_pct: float
    by_status: list[StatusError]


class InfraTimeseriesBucket(BaseModel):
    bucket: str
    requests: int
    errors: int
    p99_ms: float


class InfrastructureResponse(BaseModel):
    """Response for GET /v1/monitoring/infrastructure."""

    period: str
    generated_at: str
    request_volume: RequestVolume
    latency: Latency
    error_rates: ErrorRates
    timeseries: list[InfraTimeseriesBucket]
    alerts: list[AlertItem]


# ── GET /v1/monitoring/risk ───────────────────────────────────────────────────


class OverallApproval(BaseModel):
    total_decisions: int
    approve: int
    conditional_approve: int
    decline: int
    refer: int
    error: int
    approve_rate_pct: float
    conditional_approve_rate_pct: float
    decline_rate_pct: float


class GradeApproval(BaseModel):
    grade: str
    total: int
    approve_rate_pct: float
    decline_rate_pct: float


class ApprovalRates(BaseModel):
    overall: OverallApproval
    by_grade: list[GradeApproval]


class ScoreBucket(BaseModel):
    range: str
    count: int


class ScoreDistribution(BaseModel):
    buckets: list[ScoreBucket]
    mean: float
    median: float
    std_dev: float


class RiskTierBreakdown(BaseModel):
    tier: str
    count: int
    pct: float


class RiskTimeseriesBucket(BaseModel):
    bucket: str
    total_decisions: int
    approve_rate_pct: float
    decline_rate_pct: float
    mean_credit_score: float


class RiskResponse(BaseModel):
    """Response for GET /v1/monitoring/risk."""

    period: str
    generated_at: str
    approval_rates: ApprovalRates
    score_distribution: ScoreDistribution
    risk_tier_breakdown: list[RiskTierBreakdown]
    timeseries: list[RiskTimeseriesBucket]
    alerts: list[AlertItem]


# ── GET /v1/monitoring/model-ops ──────────────────────────────────────────────


class ChampionSummary(BaseModel):
    model_type: str
    registry_name: str
    version: str
    status: str
    created_at: str | None = None
    current_metrics: dict[str, Any] = Field(default_factory=dict)
    live_auc: float | None = None
    auc_change_pct: float | None = None
    auc_alert: bool | None = None


class FeatureDrift(BaseModel):
    feature: str
    psi: float
    status: str  # "ok" or "alert"


class RetrainingHistoryEntry(BaseModel):
    training_id: str
    completed_at: str
    model_type: str
    result: str
    metrics_before: dict[str, Any] = Field(default_factory=dict)
    metrics_after: dict[str, Any] = Field(default_factory=dict)


class ScoreDistributionPSI(BaseModel):
    model_type: str
    psi: float
    status: str


class ModelOpsResponse(BaseModel):
    """Response for GET /v1/monitoring/model-ops."""

    period: str
    generated_at: str
    champions: list[ChampionSummary]
    feature_drift: list[FeatureDrift]
    retraining_history: list[RetrainingHistoryEntry]
    score_distribution_psi: list[ScoreDistributionPSI]
    alerts: list[AlertItem]


# ── GET /v1/monitoring/compliance ─────────────────────────────────────────────


class AgeGroupApproval(BaseModel):
    group: str
    approval_rate_pct: float
    count: int


class DemographicParity(BaseModel):
    approval_rate_by_age_group: list[AgeGroupApproval]
    max_disparity_pct: float


class FairnessMetrics(BaseModel):
    demographic_parity: DemographicParity


class AuditLog(BaseModel):
    total_decisions_logged: int
    decisions_with_full_explainability: int
    coverage_pct: float
    error_decisions: int
    avg_data_quality_score: float
    dqs_p25: float


class DataSubjectRequests(BaseModel):
    total: int
    pending: int
    completed: int
    avg_resolution_days: float


class ComplianceResponse(BaseModel):
    """Response for GET /v1/monitoring/compliance."""

    period: str
    generated_at: str
    fairness_metrics: FairnessMetrics
    audit_log: AuditLog
    data_subject_requests: DataSubjectRequests
    regulation_alerts: list[Any] = Field(default_factory=list)
    alerts: list[AlertItem]


# ── GET /v1/monitoring/alerts ─────────────────────────────────────────────────


class AlertDetail(BaseModel):
    """Full alert record for the unified alert feed."""

    id: str
    metric: str
    dashboard: str
    severity: str
    status: str
    current_value: float
    threshold: float
    detail: str | None = None
    feature: str | None = None
    model_type: str | None = None
    recommended_action: str | None = None
    created_at: str
    resolved_at: str | None = None


class AlertSummary(BaseModel):
    total_firing: int
    critical_firing: int
    warning_firing: int


class AlertsResponse(BaseModel):
    """Response for GET /v1/monitoring/alerts."""

    generated_at: str
    alerts: list[AlertDetail]
    summary: AlertSummary
