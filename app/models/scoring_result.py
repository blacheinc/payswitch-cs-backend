"""Scoring Result model — stores the full 4-model scoring response from the AI pipeline.

Each record captures the complete output from the scoring-complete Service Bus topic:
credit risk, fraud detection, loan amount, income verification, decision, and metadata.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.bulk_job import BulkScoringJob
    from app.models.score_request import ScoreRequest


class ScoringDecision(enum.StrEnum):
    """Possible decisions from the AI scoring pipeline."""

    APPROVE = "APPROVE"
    CONDITIONAL_APPROVE = "CONDITIONAL_APPROVE"
    REFER = "REFER"
    DECLINE = "DECLINE"
    FRAUD_HOLD = "FRAUD_HOLD"
    ERROR = "ERROR"


class ScoringResult(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Stores the full scoring response from the AI Engineer's orchestrator."""

    __tablename__ = "scoring_results"

    # Links to ScoreRequest via tracking_id / request_id
    request_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    score_request_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("score_requests.id"), index=True
    )
    # Set when this result was ingested from a batch job's results.jsonl
    bulk_job_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bulk_scoring_jobs.id"), index=True
    )

    # Timestamps
    scoring_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Decision
    decision: Mapped[ScoringDecision] = mapped_column(
        Enum(
            ScoringDecision,
            name="scoring_decision",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )
    condition_applied: Mapped[list | None] = mapped_column(JSONB, default=list)

    # 4-model outputs (JSONB for flexibility as schema evolves)
    credit_risk: Mapped[dict | None] = mapped_column(JSONB)
    fraud_detection: Mapped[dict | None] = mapped_column(JSONB)
    loan_amount: Mapped[dict | None] = mapped_column(JSONB)  # null when Phase 2 skipped
    income_verification: Mapped[dict | None] = mapped_column(JSONB)  # null when Phase 2 skipped

    # Metadata pass-through from DE
    scoring_metadata: Mapped[dict | None] = mapped_column(JSONB)

    # Error details (only for ERROR decisions)
    errors: Mapped[list | None] = mapped_column(JSONB)

    # Relationships
    score_request: Mapped[ScoreRequest | None] = relationship(
        back_populates="scoring_result", foreign_keys=[score_request_id]
    )
    bulk_job: Mapped[BulkScoringJob | None] = relationship(foreign_keys=[bulk_job_id])

    def __repr__(self) -> str:
        return f"<ScoringResult {self.request_id} ({self.decision.value})>"
