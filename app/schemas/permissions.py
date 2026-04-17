"""Pydantic schemas for permissions and roles."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common import BaseSchema


# ── Permission schemas ────────────────────────────────────────────────────────


class PermissionResponse(BaseSchema):
    id: UUID
    code: str
    name: str
    group_name: str
    description: str
    scope: str


class PermissionListResponse(BaseModel):
    items: list[PermissionResponse]
    total: int


# ── Role schemas ──────────────────────────────────────────────────────────────


class RolePermissionItem(BaseModel):
    """Slim permission summary embedded inside a RoleResponse."""
    id: UUID
    code: str
    name: str
    group_name: str
    description: str
    scope: str

    model_config = {"from_attributes": True}


class RoleResponse(BaseSchema):
    id: UUID
    name: str
    description: str | None
    scope: str
    is_system: bool
    organization_id: UUID | None
    created_at: datetime
    permissions: list[RolePermissionItem] = []


class RoleListResponse(BaseModel):
    items: list[RoleResponse]
    total: int


class CreateRoleRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=300)
    permission_codes: list[str] = Field(
        default_factory=list,
        description="List of permission codes to assign to this role",
    )


class UpdateRoleRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=300)
    permission_codes: list[str] | None = Field(
        default=None,
        description="Full replacement permission code list (replaces existing)",
    )


class AssignRoleRequest(BaseModel):
    role_id: UUID = Field(description="ID of the role to assign")


class RoleActionResponse(BaseModel):
    """Returned by create and update role endpoints — message + full role data."""
    message: str
    role: RoleResponse
