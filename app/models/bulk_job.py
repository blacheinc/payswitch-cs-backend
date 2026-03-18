"""Bulk Scoring Job model."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
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


class BulkScoringJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "bulk_scoring_jobs"

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True
    )
    file_name: Mapped[str] = mapped_column(String(200), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[BulkJobStatus] = mapped_column(
        Enum(
            BulkJobStatus,
            name="bulk_job_status",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=BulkJobStatus.VALIDATING,
        nullable=False,
    )
    total_records: Mapped[int | None] = mapped_column(Integer)
    valid_records: Mapped[int | None] = mapped_column(Integer)
    invalid_records: Mapped[int | None] = mapped_column(Integer)
    processed_records: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    results_file_path: Mapped[str | None] = mapped_column(String(500))
    error_report_path: Mapped[str | None] = mapped_column(String(500))
    created_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("org_users.id")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    organization: Mapped["Organization"] = relationship()
    created_by: Mapped["OrgUser | None"] = relationship()
