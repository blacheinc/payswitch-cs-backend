"""Model Metric model — tracks training metrics, drift data, and live performance.

Written by:
- training subscriber (training run metrics)
- drift_calculator background job (PSI values)
- alert_evaluator (live performance snapshots)
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ModelMetric(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Tracks model training metrics, feature drift, and live performance."""

    __tablename__ = "model_metrics"

    model_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    model_version: Mapped[str | None] = mapped_column(String(50))
    training_id: Mapped[str | None] = mapped_column(String(100), index=True)

    # Type of metric record: "training", "drift", "live_performance"
    metric_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # Flexible metrics storage (AUC, F1, PSI values, etc.)
    metrics: Mapped[dict | None] = mapped_column(JSONB)

    # Per-feature PSI values for drift tracking
    feature_drift: Mapped[dict | None] = mapped_column(JSONB)

    # Timestamp for time-range queries
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    def __repr__(self) -> str:
        return (
            f"<ModelMetric {self.model_type} v{self.model_version} "
            f"type={self.metric_type}>"
        )
