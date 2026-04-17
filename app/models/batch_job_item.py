"""BatchJobItem — one DB row per applicant in a batch scoring job."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class BatchJobItemStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"   # bureau + scoring submitted (incl. hard-stop declines)
    FAILED = "failed"         # bureau lookup failed or scoring pipeline error
    CANCELLED = "cancelled"


class BatchJobItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Tracks the status of one applicant entry inside a BulkScoringJob."""

    __tablename__ = "batch_job_items"

    batch_job_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("bulk_scoring_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Zero-based position in the original submitted array
    index: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[BatchJobItemStatus] = mapped_column(
        Enum(
            BatchJobItemStatus,
            name="batch_job_item_status",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=BatchJobItemStatus.PENDING,
        nullable=False,
        index=True,
    )

    # Original bureau-lookup payload the frontend submitted
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # Populated on COMPLETED — links back to the created ScoreRequest
    score_request_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("score_requests.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Populated on COMPLETED when a hard-stop triggered — the human-readable
    # tracking ID (e.g. "SCR-ABC123") for quick cross-referencing
    score_tracking_id: Mapped[str | None] = mapped_column(String(50))

    # Populated on FAILED
    error_code: Mapped[str | None] = mapped_column(String(50))
    # "bureau_failed" | "scoring_error"
    error_message: Mapped[str | None] = mapped_column(Text)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<BatchJobItem index={self.index} status={self.status.value}>"
