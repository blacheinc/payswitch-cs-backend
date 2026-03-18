"""Audit log model (PostgreSQL for fast admin queries)."""

from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AuditLog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "audit_logs"

    # Who performed the action
    actor_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # "admin", "org_user", "api_key", "system"
    actor_id: Mapped[str | None] = mapped_column(String(50))
    actor_email: Mapped[str | None] = mapped_column(String(255))

    # What organization context (null for platform-level actions)
    organization_id: Mapped[str | None] = mapped_column(UUID(as_uuid=True), index=True)

    # What action was performed
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    resource_id: Mapped[str | None] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)

    # Request context
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    request_id: Mapped[str | None] = mapped_column(String(50))

    # Before/after state changes (for data mutation auditing)
    changes: Mapped[dict | None] = mapped_column(JSONB)

    def __repr__(self) -> str:
        return f"<AuditLog {self.action} {self.resource_type}/{self.resource_id}>"
