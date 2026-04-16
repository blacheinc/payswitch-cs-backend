"""RBAC models — Permission, Role, RolePermission, AdminUserRole, OrgUserRole."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import AdminUser, OrgUser


class Permission(UUIDPrimaryKeyMixin, Base):
    """Platform-defined permission — seeded at startup, never written by users."""

    __tablename__ = "permissions"

    # e.g. "score_requests.create"
    code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    # UI grouping label, e.g. "Scoring"
    group_name: Mapped[str] = mapped_column(String(50), nullable=False)
    # Human-readable description shown in the role builder
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    # "platform" = admin-facing only; "org" = org-facing only
    scope: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    def __repr__(self) -> str:
        return f"<Permission {self.code} ({self.scope})>"


class Role(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A named container of permissions.

    System roles:
      - SUPER_ADMIN (scope=platform, is_system=True, organization_id=None)
        → grants {"*"}, cannot be edited or deleted
      - ADMIN (scope=org, is_system=True, organization_id=None)
        → grants all org-scoped permissions, cannot be edited or deleted

    Custom roles are created by SUPER_ADMIN (platform-scoped) or
    ADMIN org users (org-scoped, organization_id=their org).
    """

    __tablename__ = "roles"
    __table_args__ = (
        # Custom org roles: unique name per org
        UniqueConstraint("name", "organization_id", name="uq_role_name_org"),
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(300))
    # "platform" or "org"
    scope: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    # None for system roles and platform-scoped custom roles
    organization_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True, index=True
    )
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(UUID(as_uuid=True))

    # Relationships
    role_permissions: Mapped[list["RolePermission"]] = relationship(
        back_populates="role", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Role {self.name} scope={self.scope} system={self.is_system}>"


class RolePermission(Base):
    """Many-to-many: Role ↔ Permission."""

    __tablename__ = "role_permissions"

    role_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    permission_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
    )

    role: Mapped["Role"] = relationship(back_populates="role_permissions")
    permission: Mapped["Permission"] = relationship()


class OrgUserRole(Base):
    """Many-to-many: OrgUser ↔ Role."""

    __tablename__ = "org_user_roles"

    org_user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("org_users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    assigned_by: Mapped[str | None] = mapped_column(UUID(as_uuid=True))
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    role: Mapped["Role"] = relationship()


class AdminUserRole(Base):
    """Many-to-many: AdminUser ↔ Role."""

    __tablename__ = "admin_user_roles"

    admin_user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    assigned_by: Mapped[str | None] = mapped_column(UUID(as_uuid=True))
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    role: Mapped["Role"] = relationship()
