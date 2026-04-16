"""Permission loader — resolves the effective permission set for a request.

Called by require_permission() before every guarded endpoint.  Results are
stored on AuthContext.permissions so they are only fetched once per request.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.permission_seeder import ALL_ORG_PERMISSION_CODES


async def load_user_permissions(db: AsyncSession, auth) -> set[str]:
    """Return the effective permission set for *auth*.

    Return values:
      {"*"}                  — SUPER_ADMIN, full platform access
      ALL_ORG_PERMISSION_CODES — org ADMIN, full org access
      {code, ...}            — custom role's explicit permission codes
      set()                  — no role assigned yet
    """
    # Already loaded this request — avoid a second DB round-trip.
    if getattr(auth, "permissions", None) is not None:
        return auth.permissions

    from app.models.permission import (
        AdminUserRole,
        OrgUserRole,
        Permission,
        Role,
        RolePermission,
    )

    user_type = auth.user_type

    if user_type == "admin":
        return await _admin_permissions(db, auth.user_id)

    if user_type == "org_user":
        return await _org_user_permissions(db, auth.user_id)

    if user_type == "api_key" and auth.api_key is not None:
        return await _api_key_permissions(db, auth.api_key)

    return set()


# ── per-type helpers ──────────────────────────────────────────────────────────


async def _admin_permissions(db: AsyncSession, user_id) -> set[str]:
    from app.models.permission import AdminUserRole, Permission, Role, RolePermission

    rows = (
        await db.execute(
            select(Role)
            .join(AdminUserRole, AdminUserRole.role_id == Role.id)
            .where(AdminUserRole.admin_user_id == user_id)
        )
    ).scalars().all()

    if not rows:
        return set()

    # Any system SUPER_ADMIN role → wildcard
    if any(r.is_system and r.name == "SUPER_ADMIN" for r in rows):
        return {"*"}

    return await _permissions_for_roles(db, [r.id for r in rows])


async def _org_user_permissions(db: AsyncSession, user_id) -> set[str]:
    from app.models.permission import OrgUserRole, Permission, Role, RolePermission

    rows = (
        await db.execute(
            select(Role)
            .join(OrgUserRole, OrgUserRole.role_id == Role.id)
            .where(OrgUserRole.org_user_id == user_id)
        )
    ).scalars().all()

    if not rows:
        return set()

    # Any system ADMIN role → all org permissions
    if any(r.is_system and r.name == "ADMIN" for r in rows):
        return set(ALL_ORG_PERMISSION_CODES)

    perms = await _permissions_for_roles(db, [r.id for r in rows])
    # Org users are hard-constrained to org-scoped permissions only
    return perms & ALL_ORG_PERMISSION_CODES


async def _api_key_permissions(db: AsyncSession, api_key) -> set[str]:
    role_id = getattr(api_key, "role_id", None)
    if role_id is None:
        # No role assigned — fall back to full org access (backwards-compat)
        return set(ALL_ORG_PERMISSION_CODES)

    from app.models.permission import Permission, Role, RolePermission

    role = (
        await db.execute(select(Role).where(Role.id == role_id))
    ).scalar_one_or_none()

    if role is None:
        return set()

    if role.is_system and role.name == "ADMIN":
        return set(ALL_ORG_PERMISSION_CODES)

    perms = await _permissions_for_roles(db, [role_id])
    return perms & ALL_ORG_PERMISSION_CODES


async def _permissions_for_roles(db: AsyncSession, role_ids: list) -> set[str]:
    """Load all permission codes assigned to the given role IDs."""
    from app.models.permission import Permission, RolePermission

    if not role_ids:
        return set()

    codes = (
        await db.execute(
            select(Permission.code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id.in_(role_ids))
        )
    ).scalars().all()

    return set(codes)
