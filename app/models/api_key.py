"""API Key model."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.user import OrgUser


class ApiKeyEnvironment(str, enum.Enum):
    SANDBOX = "sandbox"
    PRODUCTION = "production"


class ApiKeyStatus(str, enum.Enum):
    ACTIVE = "active"
    REVOKED = "revoked"


class ApiKey(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "api_keys"

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True
    )
    key_hash: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    key_prefix: Mapped[str] = mapped_column(String(12), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    environment: Mapped[ApiKeyEnvironment] = mapped_column(
        Enum(
            ApiKeyEnvironment,
            name="api_key_environment",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )
    status: Mapped[ApiKeyStatus] = mapped_column(
        Enum(
            ApiKeyStatus,
            name="api_key_status",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=ApiKeyStatus.ACTIVE,
        nullable=False,
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("org_users.id")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    organization: Mapped["Organization"] = relationship(back_populates="api_keys")
    created_by: Mapped["OrgUser | None"] = relationship()

    def __repr__(self) -> str:
        return f"<ApiKey {self.key_prefix}... ({self.environment.value}/{self.status.value})>"
