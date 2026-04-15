"""Bulk Scoring Job model."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.organization import Organization
from app.models.user import OrgUser


class BulkJobStatus(str, enum.Enum):
    VALIDATING = "validating"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class IngestionStatus(str, enum.Enum):
    PENDING = "pending"      # results not yet available
    INGESTING = "ingesting"  # reading JSONL blob, writing ScoringResult rows
    COMPLETE = "complete"    # all rows written, results queryable
    FAILED = "failed"        # ingestion error


class BulkScoringJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "bulk_scoring_jobs"

    # External tracking ID used in Service Bus messages and API responses
    # e.g. "BATCH-20260411-abc12345"
    job_id: Mapped[str | None] = mapped_column(String(100), unique=True, index=True)

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True
    )
    file_name: Mapped[str | None] = mapped_column(String(200))
    file_path: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[BulkJobStatus] = mapped_column(
        Enum(
            BulkJobStatus,
            name="bulk_job_status",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=BulkJobStatus.QUEUED,
        nullable=False,
    )
    # Which models the orchestrator should run
    models_to_run: Mapped[list | None] = mapped_column(JSONB)

    # Record counts
    total_records: Mapped[int | None] = mapped_column(Integer)
    valid_records: Mapped[int | None] = mapped_column(Integer)
    invalid_records: Mapped[int | None] = mapped_column(Integer)
    processed_records: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)

    # Blob paths written by the orchestrator
    results_blob_path: Mapped[str | None] = mapped_column(String(500))
    # e.g. "batch/BATCH-20260411-001/results.jsonl"

    results_file_path: Mapped[str | None] = mapped_column(String(500))
    error_report_path: Mapped[str | None] = mapped_column(String(500))

    # Result ingestion tracking (JSONL → ScoringResult rows)
    ingestion_status: Mapped[IngestionStatus] = mapped_column(
        Enum(
            IngestionStatus,
            name="ingestion_status",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=IngestionStatus.PENDING,
        nullable=False,
    )
    ingested_count: Mapped[int] = mapped_column(Integer, default=0)

    created_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("org_users.id")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    organization: Mapped["Organization"] = relationship()
    created_by: Mapped["OrgUser | None"] = relationship()
