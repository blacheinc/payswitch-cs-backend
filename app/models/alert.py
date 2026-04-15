"""Alert model — persistent alert records for threshold breaches.

Created/resolved by the alert_evaluator background job.
Read by GET /v1/monitoring/alerts and inline in each dashboard response.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Alert(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Persistent alert record for threshold breaches across all dashboards."""

    __tablename__ = "alerts"

    # Human-readable alert ID (e.g. "ALT-20260414-001")
    alert_id: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )

    # What metric triggered this alert
    metric: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    # Which dashboard owns this alert
    dashboard: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # Severity: "warning" or "critical"
    severity: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    # Status: "firing", "resolved", "ok"
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    # Current metric value when alert was created/updated
    current_value: Mapped[float] = mapped_column(Float, nullable=False)

    # Threshold that was breached
    threshold: Mapped[float] = mapped_column(Float, nullable=False)

    # Human-readable description
    detail: Mapped[str | None] = mapped_column(Text)

    # Optional context for feature/model-specific alerts
    feature: Mapped[str | None] = mapped_column(String(100))
    model_type: Mapped[str | None] = mapped_column(String(50))

    # Recommended action
    recommended_action: Mapped[str | None] = mapped_column(Text)

    # Resolution timestamp
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<Alert {self.alert_id} {self.metric} [{self.status}]>"
