"""Permissions and Roles management router.

Endpoints:
  GET  /v1/permissions          — list permissions visible to the caller
  GET  /v1/roles                — list roles visible to the caller
  POST /v1/roles                — create a custom role
  GET  /v1/roles/{role_id}      — get a role with its permission list
  PATCH /v1/roles/{role_id}     — update name, description, or permission set
  DELETE /v1/roles/{role_id}    — delete a non-system role

Access rules:
  - Admin users: manage platform-scoped roles; see all 47 permissions.
    Required permission: admin.roles.read / admin.roles.manage
  - Org users: manage org-scoped roles for their own organization; see only
    org-scoped permissions.
    Required permission: roles.read / roles.manage
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.db import get_db
from app.dependencies import AuthContext, require_any_permission
from app.models.permission import Permission, Role, RolePermission
from app.schemas.common import SuccessResponse
from app.schemas.permissions import (
    CreateRoleRequest,
    PermissionListResponse,
    PermissionResponse,
    RoleActionResponse,
    RoleListResponse,
    RolePermissionItem,
    RoleResponse,
    UpdateRoleRequest,
)
from app.services.permission_seeder import ALL_ORG_PERMISSION_CODES, ALL_PERMISSION_CODES

router = APIRouter(prefix="/v1", tags=["Permissions & Roles"])

# ── Permission exclusions ─────────────────────────────────────────────────────
# Permissions whose group_name appears here are omitted from GET /v1/permissions.
# They still exist in the DB and are enforced server-side — they just aren't
# surfaced in the role builder UI.  Add/remove group names here as needed.
EXCLUDED_PERMISSION_GROUPS: frozenset[str] = frozenset(
    [
        "API Keys",
        "Webhooks",
    ]
)


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _role_to_response(db: AsyncSession, role: Role) -> RoleResponse:
    """Load a Role's permissions and return a RoleResponse."""
    perms = (
        (
            await db.execute(
                select(Permission)
                .join(RolePermission, RolePermission.permission_id == Permission.id)
                .where(RolePermission.role_id == role.id)
            )
        )
        .scalars()
        .all()
    )

    return RoleResponse(
        id=role.id,
        name=role.name,
        description=role.description,
        scope=role.scope,
        is_system=role.is_system,
        organization_id=role.organization_id,
        created_at=role.created_at,
        permissions=[RolePermissionItem.model_validate(p) for p in perms],
    )


def _assert_can_manage(auth: AuthContext, role: Role) -> None:
    """Raise if the caller is not allowed to modify *role*."""
    if role.is_system:
        raise ValidationError("System roles cannot be modified or deleted")
    if auth.user_type == "org_user":
        if str(role.organization_id) != str(auth.organization_id):
            raise NotFoundError("Role", str(role.id))
    # Admin users can manage any non-system platform role (org_id = None)
    if auth.user_type == "admin" and role.organization_id is not None:
        raise AuthorizationError("Platform admins cannot modify organization roles")


# ── Permissions list ──────────────────────────────────────────────────────────


@router.get("/permissions", response_model=PermissionListResponse)
async def list_permissions(
    auth: Annotated[AuthContext, Depends(require_any_permission("admin.roles.read", "roles.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PermissionListResponse:
    """List all permissions visible to the caller.

    - Platform admins: all 47 permissions (both scopes).
    - Org users: org-scoped permissions only (27).
    """
    if auth.user_type == "admin":
        rows = (
            (
                await db.execute(
                    select(Permission).where(
                        Permission.group_name.not_in(EXCLUDED_PERMISSION_GROUPS)
                    )
                )
            )
            .scalars()
            .all()
        )
    else:
        rows = (
            (
                await db.execute(
                    select(Permission).where(
                        Permission.scope == "org",
                        Permission.group_name.not_in(EXCLUDED_PERMISSION_GROUPS),
                    )
                )
            )
            .scalars()
            .all()
        )

    return PermissionListResponse(
        items=[PermissionResponse.model_validate(p) for p in rows],
        total=len(rows),
    )


# ── Roles ─────────────────────────────────────────────────────────────────────


@router.get("/roles", response_model=RoleListResponse)
async def list_roles(
    auth: Annotated[AuthContext, Depends(require_any_permission("admin.roles.read", "roles.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RoleListResponse:
    """List roles visible to the caller.

    - Platform admins: platform-scoped roles (is_system SUPER_ADMIN + custom platform roles).
    - Org users: org-scoped system role (ADMIN) + custom roles belonging to their org.
    """
    if auth.user_type == "admin":
        rows = (
            (
                await db.execute(
                    select(Role).where(
                        Role.scope == "platform",
                        Role.organization_id.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
    else:
        rows = (
            (
                await db.execute(
                    select(Role).where(
                        Role.scope == "org",
                        (Role.organization_id == auth.organization_id) | (Role.is_system == True),  # noqa: E712
                    )
                )
            )
            .scalars()
            .all()
        )

    items = [await _role_to_response(db, r) for r in rows]
    return RoleListResponse(items=items, total=len(items))


@router.post("/roles", response_model=RoleActionResponse, status_code=status.HTTP_201_CREATED)
async def create_role(
    body: CreateRoleRequest,
    auth: Annotated[
        AuthContext, Depends(require_any_permission("admin.roles.manage", "roles.manage"))
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RoleActionResponse:
    """Create a custom role.

    - Platform admins create platform-scoped roles (organization_id = None).
    - Org users create org-scoped roles (organization_id = their org).
    """
    if auth.user_type == "admin":
        scope = "platform"
        org_id = None
    else:
        scope = "org"
        org_id = auth.organization_id

    # Validate permission codes exist and are in-scope
    allowed_codes = ALL_PERMISSION_CODES if auth.user_type == "admin" else ALL_ORG_PERMISSION_CODES
    invalid = [c for c in body.permission_codes if c not in allowed_codes]
    if invalid:
        raise ValidationError(f"Unknown or out-of-scope permission codes: {', '.join(invalid)}")

    # Check unique name within scope/org
    existing = (
        await db.execute(
            select(Role).where(
                Role.name == body.name,
                Role.organization_id == org_id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        raise ConflictError(f"A role named '{body.name}' already exists")

    role = Role(
        name=body.name,
        description=body.description,
        scope=scope,
        organization_id=org_id,
        is_system=False,
        created_by_user_id=auth.user_id,
    )
    db.add(role)
    await db.flush()

    if body.permission_codes:
        perm_rows = (
            (await db.execute(select(Permission).where(Permission.code.in_(body.permission_codes))))
            .scalars()
            .all()
        )
        for p in perm_rows:
            db.add(RolePermission(role_id=role.id, permission_id=p.id))
        await db.flush()

    role_response = await _role_to_response(db, role)
    return RoleActionResponse(message=f"Role '{role.name}' created", role=role_response)


@router.get("/roles/{role_id}", response_model=RoleResponse)
async def get_role(
    role_id: UUID,
    auth: Annotated[AuthContext, Depends(require_any_permission("admin.roles.read", "roles.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RoleResponse:
    role = (await db.execute(select(Role).where(Role.id == role_id))).scalar_one_or_none()

    if not role:
        raise NotFoundError("Role", str(role_id))

    # Org users can only see their org's roles and the system ADMIN role
    if auth.user_type == "org_user":
        if not role.is_system and str(role.organization_id) != str(auth.organization_id):
            raise NotFoundError("Role", str(role_id))

    return await _role_to_response(db, role)


@router.patch("/roles/{role_id}", response_model=RoleActionResponse)
async def update_role(
    role_id: UUID,
    body: UpdateRoleRequest,
    auth: Annotated[
        AuthContext, Depends(require_any_permission("admin.roles.manage", "roles.manage"))
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RoleActionResponse:
    role = (await db.execute(select(Role).where(Role.id == role_id))).scalar_one_or_none()

    if not role:
        raise NotFoundError("Role", str(role_id))

    _assert_can_manage(auth, role)

    if body.name is not None:
        role.name = body.name
    if body.description is not None:
        role.description = body.description

    if body.permission_codes is not None:
        allowed_codes = (
            ALL_PERMISSION_CODES if auth.user_type == "admin" else ALL_ORG_PERMISSION_CODES
        )
        invalid = [c for c in body.permission_codes if c not in allowed_codes]
        if invalid:
            raise ValidationError(f"Unknown or out-of-scope permission codes: {', '.join(invalid)}")

        # Delete existing RolePermissions and re-insert
        existing_rp = (
            (await db.execute(select(RolePermission).where(RolePermission.role_id == role.id)))
            .scalars()
            .all()
        )
        for rp in existing_rp:
            await db.delete(rp)
        await db.flush()

        if body.permission_codes:
            perm_rows = (
                (
                    await db.execute(
                        select(Permission).where(Permission.code.in_(body.permission_codes))
                    )
                )
                .scalars()
                .all()
            )
            for p in perm_rows:
                db.add(RolePermission(role_id=role.id, permission_id=p.id))

    await db.flush()
    await db.refresh(role)
    role_response = await _role_to_response(db, role)
    return RoleActionResponse(message=f"Role '{role.name}' updated", role=role_response)


@router.delete("/roles/{role_id}", response_model=SuccessResponse)
async def delete_role(
    role_id: UUID,
    auth: Annotated[
        AuthContext, Depends(require_any_permission("admin.roles.manage", "roles.manage"))
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SuccessResponse:
    role = (await db.execute(select(Role).where(Role.id == role_id))).scalar_one_or_none()

    if not role:
        raise NotFoundError("Role", str(role_id))

    _assert_can_manage(auth, role)

    await db.delete(role)
    return SuccessResponse(message=f"Role '{role.name}' deleted")
