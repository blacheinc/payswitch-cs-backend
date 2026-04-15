"""Decision Metric model — denormalized scoring decision data for dashboards.

Populated inline by the scoring_complete subscriber on every decision.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class DecisionMetric(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Denormalized scoring decision record for fast dashboard aggregation."""

    __tablename__ = "decision_metrics"

    scoring_result_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scoring_results.id"), unique=True, index=True
    )

    # Decision outcome
    decision: Mapped[str] = mapped_column(String(30), nullable=False, index=True)

    # Score data
    credit_score: Mapped[int | None] = mapped_column(Integer)
    score_grade: Mapped[str | None] = mapped_column(String(5), index=True)
    risk_tier: Mapped[str | None] = mapped_column(String(20), index=True)
    fraud_flag: Mapped[str | None] = mapped_column(String(20))
    loan_tier: Mapped[str | None] = mapped_column(String(20))
    probability_of_default: Mapped[float | None] = mapped_column(Float)

    # Data quality
    data_quality_score: Mapped[float | None] = mapped_column(Float)

    # Compliance / fairness
    applicant_age: Mapped[int | None] = mapped_column(Integer)

    # Explainability coverage
    has_explainability: Mapped[bool | None] = mapped_column(Boolean, default=False)

    # Timestamp for time-range queries
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    def __repr__(self) -> str:
        return f"<DecisionMetric {self.decision} score={self.credit_score}>"
