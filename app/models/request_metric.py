"""Request Metric model — lightweight, org-agnostic request telemetry.

Captures ALL HTTP requests (including unauthenticated) for infrastructure
dashboard aggregation. Distinct from ApiLog which is org-scoped.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class RequestMetric(UUIDPrimaryKeyMixin, Base):
    """Lightweight record of every HTTP request for infrastructure metrics."""

    __tablename__ = "request_metrics"

    endpoint: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    response_time_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    request_id: Mapped[str | None] = mapped_column(String(50))

    def __repr__(self) -> str:
        return f"<RequestMetric {self.method} {self.endpoint} {self.status_code}>"
