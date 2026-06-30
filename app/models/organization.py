"""Organization model."""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING

from sqlalchemy import Enum, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.api_key import ApiKey
    from app.models.score_request import ScoreRequest
    from app.models.user import OrgUser
    from app.models.webhook import WebhookConfig


class IndustryType(enum.StrEnum):
    BANK = "bank"
    FINTECH = "fintech"
    MFI = "mfi"
    SACCO = "sacco"
    OTHER = "other"


class OrgStatus(enum.StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    short_name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    industry_type: Mapped[IndustryType] = mapped_column(
        Enum(
            IndustryType,
            name="industry_type",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )
    primary_contact_name: Mapped[str | None] = mapped_column(String(200))
    primary_contact_email: Mapped[str | None] = mapped_column(String(255))
    primary_contact_phone: Mapped[str | None] = mapped_column(String(20))
    address: Mapped[str | None] = mapped_column(Text)
    status: Mapped[OrgStatus] = mapped_column(
        Enum(OrgStatus, name="org_status", values_callable=lambda x: [e.value for e in x]),
        default=OrgStatus.PENDING,
        nullable=False,
    )
    suspension_reason: Mapped[str | None] = mapped_column(Text)
    webhook_secret: Mapped[str | None] = mapped_column(String(64))

    # Relationships
    users: Mapped[list[OrgUser]] = relationship(back_populates="organization", lazy="selectin")
    api_keys: Mapped[list[ApiKey]] = relationship(back_populates="organization", lazy="selectin")
    score_requests: Mapped[list[ScoreRequest]] = relationship(
        back_populates="organization", lazy="noload"
    )
    webhook_configs: Mapped[list[WebhookConfig]] = relationship(
        back_populates="organization", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<Organization {self.short_name} ({self.status.value})>"
