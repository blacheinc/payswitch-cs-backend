"""User models – AdminUser (platform staff) and OrgUser (organization members)."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.organization import Organization


class AdminRole(enum.StrEnum):
    SUPER_ADMIN = "super_admin"
    DATA_SCIENCE = "data_science"
    OPERATIONS = "operations"


class AdminUserStatus(enum.StrEnum):
    ACTIVE = "active"
    DEACTIVATED = "deactivated"


class AdminUser(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """PaySwitch platform administrator."""

    __tablename__ = "admin_users"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[AdminRole] = mapped_column(
        Enum(AdminRole, name="admin_role", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    status: Mapped[AdminUserStatus] = mapped_column(
        Enum(
            AdminUserStatus,
            name="admin_user_status",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=AdminUserStatus.ACTIVE,
        nullable=False,
    )
    totp_secret: Mapped[str | None] = mapped_column(String(32))
    totp_enabled: Mapped[bool] = mapped_column(default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_reset_token: Mapped[str | None] = mapped_column(String(64))
    password_reset_expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set when the account is issued a system-generated password; login
    # refuses to hand out a full token until it is cleared (VAPT finding 6).
    must_change_password: Mapped[bool] = mapped_column(default=False, nullable=False)

    def __repr__(self) -> str:
        return f"<AdminUser {self.email} ({self.role.value})>"


class OrgUserStatus(enum.StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REMOVED = "removed"


class OrgRoleLabel(enum.StrEnum):
    """Labels only – no access restrictions enforced (per PRD)."""

    ADMIN = "admin"
    CREDIT_OFFICER = "credit_officer"
    DEVELOPER = "developer"
    VIEWER = "viewer"


class OrgUser(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """User belonging to a B2B organization."""

    __tablename__ = "org_users"

    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    role_label: Mapped[OrgRoleLabel] = mapped_column(
        Enum(
            OrgRoleLabel,
            name="org_role_label",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=OrgRoleLabel.VIEWER,
        nullable=False,
    )
    status: Mapped[OrgUserStatus] = mapped_column(
        Enum(
            OrgUserStatus,
            name="org_user_status",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=OrgUserStatus.PENDING,
        nullable=False,
    )
    totp_secret: Mapped[str | None] = mapped_column(String(32))
    totp_enabled: Mapped[bool] = mapped_column(default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    email_verification_token: Mapped[str | None] = mapped_column(String(64))
    password_reset_token: Mapped[str | None] = mapped_column(String(64))
    password_reset_expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set when the account is issued a system-generated password; login
    # refuses to hand out a full token until it is cleared (VAPT finding 6).
    must_change_password: Mapped[bool] = mapped_column(default=False, nullable=False)

    # Relationships
    organization: Mapped[Organization] = relationship(back_populates="users")

    def __repr__(self) -> str:
        return f"<OrgUser {self.email} org={self.organization_id}>"
