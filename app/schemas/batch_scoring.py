"""Schemas for the redesigned batch scoring endpoints."""

from __future__ import annotations

from datetime import date
from pydantic import BaseModel, Field


# ── Request ───────────────────────────────────────────────────────────────────


class BatchItemPayload(BaseModel):
    """Personal details for one applicant — mirrors BureauLookupRequest."""

    full_name: str | None = Field(
        default=None, min_length=2, max_length=200,
        description="Applicant full name. Optional if identification is provided.",
    )
    date_of_birth: date = Field(description="Applicant date of birth")
    identification: str | None = Field(
        default=None, max_length=50,
        description="National ID / Ghana Card number",
    )
    phone_number: str | None = Field(
        default=None, pattern=r"^\+?[0-9]{10,15}$",
        description="Mobile number for Product 49 lookup",
    )
    account_number: str | None = Field(default=None, max_length=50)
    enquiry_reason: str = Field(
        default="Application for credit by a borrower",
        description="XDS enquiry reason — defaults to standard credit application",
    )


class BatchScoreRequest(BaseModel):
    items: list[BatchItemPayload] = Field(
        ..., min_length=1, max_length=100,
        description="Array of applicants to score. Maximum 100 per batch.",
    )


# ── Responses ─────────────────────────────────────────────────────────────────


class BatchJobSubmitted(BaseModel):
    job_id: str
    status: str = "queued"
    total: int
    requested_at: str


class BatchItemCounts(BaseModel):
    pending: int = 0
    processing: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0


class BatchJobStatusResponse(BaseModel):
    job_id: str
    status: str          # queued | processing | completed | failed | cancelled
    total: int
    progress_pct: int
    items: BatchItemCounts
    requested_at: str
    started_at: str | None = None
    completed_at: str | None = None


class BatchItemResult(BaseModel):
    """One row in the results table."""
    index: int
    status: str          # completed | failed | pending | processing | cancelled
    score_request_id: str | None = None
    score_tracking_id: str | None = None
    # "bureau_failed" | "scoring_error" — only on failed items
    error_code: str | None = None
    error_message: str | None = None
    # Original payload echoed back so the FE can identify the applicant
    payload: dict | None = None
    completed_at: str | None = None


class BatchResultsPage(BaseModel):
    job_id: str
    items: list[BatchItemResult]
    total: int
    page: int
    page_size: int


class BatchJobListItem(BaseModel):
    job_id: str
    status: str
    total: int
    completed: int
    failed: int
    requested_at: str
    completed_at: str | None = None


class BatchJobList(BaseModel):
    items: list[BatchJobListItem]
    total: int
    page: int
    page_size: int
