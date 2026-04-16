"""User schemas."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from app.schemas.common import BaseSchema


class RoleLabel(str, Enum):
    """Valid org user roles."""

    ADMIN = "admin"
    CREDIT_OFFICER = "credit_officer"
    DEVELOPER = "developer"
    VIEWER = "viewer"


class InviteUserRequest(BaseModel):
    email: EmailStr
    name: str = Field(max_length=200)
    role_label: RoleLabel = Field(
        default=RoleLabel.VIEWER,
        description="User role label: admin, credit_officer, developer, viewer",
    )
    role_id: UUID | None = Field(
        default=None,
        description="RBAC role to assign. Defaults to the system ADMIN role when omitted.",
    )
    callback_url: str | None = None


class UpdateUserRequest(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    role_label: RoleLabel | None = Field(
        default=None,
        description="User role label: admin, credit_officer, developer, viewer",
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
    email: EmailStr
    name: str = Field(max_length=200)
    role: str = Field(description="super_admin, data_science, operations")
    password: str = Field(min_length=8, max_length=128)


class OrgUserListResponse(BaseModel):
    items: list[OrgUserResponse]
    total: int
    page: int
    per_page: int
    total_pages: int
    total_members: int
    pending_invites: int
    admin_users: int
