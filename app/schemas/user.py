"""User schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from app.schemas.common import BaseSchema


class InviteUserRequest(BaseModel):
    email: EmailStr
    name: str = Field(max_length=200)
    # Display-only badge — no longer enum-validated since RBAC role_id
    # is the authoritative permissions mechanism.  Any string is accepted;
    # unknown values are silently coerced to "viewer" in the handler.
    role_label: str | None = Field(
        default=None,
        description="Optional display label (e.g. 'credit_officer'). Defaults to 'viewer'.",
    )
    role_id: UUID | None = Field(
        default=None,
        description="RBAC role to assign. Defaults to the system ADMIN role when omitted.",
    )
    callback_url: str | None = None


class UpdateUserRequest(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    role_label: str | None = Field(
        default=None,
        description="Optional display label update. Unknown values are coerced to 'viewer'.",
    )
    role_id: UUID | None = Field(
        default=None,
        description="Replace the user's RBAC role assignment",
    )


class OrgUserResponse(BaseSchema):
    id: UUID
    email: str
    name: str
    role_label: str
    status: str
    last_login_at: datetime | None
    created_at: datetime


class AdminUserResponse(BaseSchema):
    id: UUID
    email: str
    name: str
    role: str
    status: str
    last_login_at: datetime | None
    created_at: datetime


class CreateAdminUserRequest(BaseModel):
    """Legacy direct-create schema used by CLI bootstrap scripts.

    New workflows should prefer :class:`InviteAdminRequest` via the
    ``POST /admin/admins`` endpoint, which issues a temp password, emails
    the invitee, and assigns an RBAC role (not the legacy enum).
    """
    email: EmailStr
    name: str = Field(max_length=200)
    role: str = Field(
        default="super_admin",
        description=(
            "Legacy display badge only. The authoritative permission grant "
            "is the RBAC role assignment attached at user-create time."
        ),
    )
    password: str = Field(min_length=8, max_length=128)


class InviteAdminRequest(BaseModel):
    """Invite a new platform administrator.

    Mirrors :class:`InviteUserRequest` but targets the AdminUser table and
    only accepts platform-scoped RBAC roles. When ``role_id`` is omitted
    the invitee is assigned the system **SUPER_ADMIN** role by default —
    callers who want a narrower scope should pass an explicit role_id.
    """
    email: EmailStr
    name: str = Field(max_length=200)
    role_id: UUID | None = Field(
        default=None,
        description=(
            "Platform-scoped RBAC role to assign. Defaults to the system "
            "SUPER_ADMIN role when omitted."
        ),
    )
    callback_url: str | None = None


class UpdateAdminRequest(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    role_id: UUID | None = Field(
        default=None,
        description="Replace the admin's RBAC role assignment",
    )


class AdminUserDetailResponse(BaseSchema):
    """Full admin profile returned from /admin/admins endpoints.

    Unlike the legacy :class:`AdminUserResponse` this includes the resolved
    RBAC role (`role_id`, `role_name`) rather than the retired enum badge.
    """
    id: UUID
    email: str
    name: str
    status: str
    role_id: UUID | None
    role_name: str | None
    last_login_at: datetime | None
    created_at: datetime


class AdminUserListResponse(BaseModel):
    items: list[AdminUserDetailResponse]
    total: int
    page: int
    per_page: int
    total_pages: int


class OrgUserListResponse(BaseModel):
    items: list[OrgUserResponse]
    total: int
    page: int
    per_page: int
    total_pages: int
    total_members: int
    pending_invites: int
    admin_users: int
