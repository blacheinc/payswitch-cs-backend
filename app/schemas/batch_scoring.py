"""Schemas for batch scoring endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── Request ───────────────────────────────────────────────────────────────────

class BatchApplicant(BaseModel):
    request_id: str = Field(..., description="Caller-supplied applicant identifier")
    features: dict[str, float] = Field(
        default_factory=dict,
        description="Feature vector — numeric values, e.g. {'utilisation_ratio': 0.78}",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Scoring metadata — credit_score, score_grade, decision_label, etc.",
    )


class BatchScoreRequest(BaseModel):
    applicants: list[BatchApplicant] = Field(
        ..., min_length=1, max_length=1000,
        description="List of applicants to score",
    )
    models_to_run: list[str] = Field(
        default=["all"],
        description='Which models to run: ["all"] or specific model types',
    )


# ── Responses ─────────────────────────────────────────────────────────────────

class BatchJobSubmitted(BaseModel):
    job_id: str
    status: str  # "queued"
    total: int
    requested_at: str


class BatchJobStatus(BaseModel):
    job_id: str
    status: str           # "queued" | "in_progress" | "complete" | "failed" | "cancelled"
    total: int | None
    completed: int | None
    failed: int | None
    progress_pct: int | None
    ingestion_status: str | None  # "pending" | "ingesting" | "complete" | "failed"
    ingested_count: int | None
    requested_at: str
    updated_at: str | None
    completed_at: str | None


class BatchResultItem(BaseModel):
    """One row in the results table — key fields for rendering."""
    request_id: str
    decision: str
    credit_score: int | None
    score_grade: str | None
    risk_tier: str | None
    probability_of_default: float | None
    recommended_amount_ghs: float | None
    fraud_risk_flag: str | None
    scoring_timestamp: str | None


class BatchResultsPage(BaseModel):
    job_id: str
    ingestion_status: str
    items: list[BatchResultItem]
    total: int
    page: int
    page_size: int


class BatchJobListItem(BaseModel):
    job_id: str
    status: str
    total: int | None
    completed: int | None
    failed: int | None
    ingestion_status: str
    requested_at: str
    completed_at: str | None


class BatchJobList(BaseModel):
    items: list[BatchJobListItem]
    total: int
    page: int
    page_size: int
