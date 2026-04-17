"""API Log model – tracks every /v1/ API request per organization."""

from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ApiLog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Persisted log of every organisation-facing API request."""

    __tablename__ = "api_logs"

    # Organisation scope — NULL for platform (admin) logs
    organization_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True, index=True
    )

    # Request metadata
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    response_time_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    # Client context
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    request_id: Mapped[str | None] = mapped_column(String(50))

    # Auth context
    api_key_id: Mapped[str | None] = mapped_column(UUID(as_uuid=True))
    api_key_prefix: Mapped[str | None] = mapped_column(String(12))
    user_id: Mapped[str | None] = mapped_column(UUID(as_uuid=True))

    # Error detail (only populated on 4xx/5xx)
    error_detail: Mapped[str | None] = mapped_column(Text)

    def __repr__(self) -> str:
        return f"<ApiLog {self.method} {self.path} {self.status_code}>"
