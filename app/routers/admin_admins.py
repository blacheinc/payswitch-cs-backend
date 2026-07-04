"""Admin-user management router — platform admin CRUD.

Mirrors the org user CRUD in ``app/routers/org_portal.py`` but:

* Targets :class:`AdminUser` (platform staff), not :class:`OrgUser`.
* Only accepts **platform-scoped** RBAC roles (no organization_id binding).
* Enforces a "last active SUPER_ADMIN" invariant so the platform cannot
  be locked out of administration by suspending, demoting, or deleting
  the final SUPER_ADMIN.

The bootstrap script (``scripts/create_superuser.py``) remains the only
way to mint the very first admin — these HTTP endpoints all require an
authenticated caller holding the matching ``admin.admins.*`` permission.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.security import generate_temp_password, hash_password
from app.db import get_db
from app.dependencies import AuthContext, require_permission
from app.models.permission import AdminUserRole, Role
from app.models.user import AdminUser, AdminUserStatus
from app.schemas.common import SuccessResponse
from app.schemas.user import (
    AdminUserDetailResponse,
    AdminUserListResponse,
    InviteAdminRequest,
    UpdateAdminRequest,
)
from app.services.email import email_service

router = APIRouter(prefix="/admin/admins", tags=["Platform Admin Users"])


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _resolve_platform_role(db: AsyncSession, role_id: UUID | None) -> Role:
    """Return the platform-scoped :class:`Role` to assign to an admin.

    * If ``role_id`` is provided it must exist and be ``scope == "platform"``.
    * If ``role_id`` is omitted, the system SUPER_ADMIN role is used — this
      preserves the existing bootstrap semantics where the first admin is
      a super admin unless the caller asks for something narrower.
    """
    if role_id is not None:
        role = (await db.execute(select(Role).where(Role.id == role_id))).scalar_one_or_none()
        if not role:
            raise NotFoundError("Role", str(role_id))
        if role.scope != "platform":
            raise ValidationError("Only platform-scoped roles can be assigned to platform admins")
        return role

    super_admin = (
        await db.execute(
            select(Role).where(
                Role.name == "SUPER_ADMIN",
                Role.is_system == True,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if not super_admin:
        raise ValidationError("System SUPER_ADMIN role not found — ensure seeder has run")
    return super_admin


async def _assert_not_last_super_admin(
    db: AsyncSession, admin_user_id: UUID, *, reason: str
) -> None:
    """Guard that refuses operations leaving zero active SUPER_ADMINs.

    Called before:
      * suspending/removing an admin,
      * reassigning an admin's role away from SUPER_ADMIN.

    Raises :class:`ConflictError` (409) when violation detected.
    """
    super_admin = (
        await db.execute(
            select(Role).where(
                Role.name == "SUPER_ADMIN",
                Role.is_system == True,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if not super_admin:
        return  # seeder hasn't run — nothing to guard

    # Is the subject currently a SUPER_ADMIN?
    is_super = (
        await db.execute(
            select(AdminUserRole).where(
                AdminUserRole.admin_user_id == admin_user_id,
                AdminUserRole.role_id == super_admin.id,
            )
        )
    ).scalar_one_or_none()
    if not is_super:
        return  # not a super admin, nothing to guard

    # How many ACTIVE super admins exist?
    active_super_count = (
        await db.execute(
            select(func.count())
            .select_from(AdminUserRole)
            .join(AdminUser, AdminUser.id == AdminUserRole.admin_user_id)
            .where(
                AdminUserRole.role_id == super_admin.id,
                AdminUser.status == AdminUserStatus.ACTIVE,
            )
        )
    ).scalar_one()

    if active_super_count <= 1:
        raise ConflictError(
            f"Cannot {reason}: this is the last active SUPER_ADMIN. "
            "Promote another admin to SUPER_ADMIN first."
        )


async def _detail_response(db: AsyncSession, admin: AdminUser) -> AdminUserDetailResponse:
    """Hydrate an admin row with its RBAC role for the API response."""
    role_row = (
        (
            await db.execute(
                select(Role)
                .join(AdminUserRole, AdminUserRole.role_id == Role.id)
                .where(AdminUserRole.admin_user_id == admin.id)
            )
        )
        .scalars()
        .first()
    )

    return AdminUserDetailResponse(
        id=admin.id,
        email=admin.email,
        name=admin.name,
        status=admin.status.value,
        role_id=role_row.id if role_row else None,
        role_name=role_row.name if role_row else None,
        last_login_at=admin.last_login_at,
        created_at=admin.created_at,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=AdminUserDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
async def invite_admin(
    body: InviteAdminRequest,
    auth: Annotated[AuthContext, Depends(require_permission("admin.admins.invite"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AdminUserDetailResponse:
    """Invite a new platform administrator.

    Creates an ACTIVE admin with a temp password, assigns the requested
    platform RBAC role (defaults to SUPER_ADMIN), and emails the invitee.
    """
    role = await _resolve_platform_role(db, body.role_id)

    existing = (
        await db.execute(select(AdminUser).where(AdminUser.email == body.email))
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError(f"Admin with email '{body.email}' already exists")

    temp_password = generate_temp_password()

    # role_label enum column is legacy but still NOT NULL on the table —
    # set something sensible from the RBAC role we actually care about.
    from app.models.user import AdminRole

    legacy_role = AdminRole.SUPER_ADMIN if role.name == "SUPER_ADMIN" else AdminRole.OPERATIONS

    new_admin = AdminUser(
        email=body.email,
        name=body.name,
        password_hash=hash_password(temp_password),
        role=legacy_role,
        status=AdminUserStatus.ACTIVE,
    )
    db.add(new_admin)
    await db.flush()

    db.add(
        AdminUserRole(
            admin_user_id=new_admin.id,
            role_id=role.id,
            assigned_by=auth.user_id,
        )
    )
    await db.flush()

    email_service.send_invite_email(
        to_email=new_admin.email,
        temp_password=temp_password,
        callback_url=body.callback_url,
    )

    return await _detail_response(db, new_admin)


@router.get("", response_model=AdminUserListResponse)
async def list_admins(
    auth: Annotated[AuthContext, Depends(require_permission("admin.admins.list"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
) -> AdminUserListResponse:
    """List all platform admins (paginated, newest first)."""
    total = (await db.execute(select(func.count()).select_from(AdminUser))).scalar_one()

    result = await db.execute(
        select(AdminUser)
        .order_by(AdminUser.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    admins = result.scalars().all()

    items = [await _detail_response(db, a) for a in admins]

    return AdminUserListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=(total + per_page - 1) // per_page if per_page > 0 else 0,
    )


@router.get("/{admin_id}", response_model=AdminUserDetailResponse)
async def get_admin(
    admin_id: UUID,
    auth: Annotated[AuthContext, Depends(require_permission("admin.admins.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AdminUserDetailResponse:
    admin = (
        await db.execute(select(AdminUser).where(AdminUser.id == admin_id))
    ).scalar_one_or_none()
    if admin is None:
        raise NotFoundError("Admin", str(admin_id))
    return await _detail_response(db, admin)


@router.patch("/{admin_id}", response_model=AdminUserDetailResponse)
async def update_admin(
    admin_id: UUID,
    body: UpdateAdminRequest,
    auth: Annotated[AuthContext, Depends(require_permission("admin.admins.update"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AdminUserDetailResponse:
    """Update an admin's name and/or RBAC role.

    Reassigning away from SUPER_ADMIN is blocked if this is the last one.
    """
    admin = (
        await db.execute(select(AdminUser).where(AdminUser.id == admin_id))
    ).scalar_one_or_none()
    if admin is None:
        raise NotFoundError("Admin", str(admin_id))

    update_data = body.model_dump(exclude_unset=True)
    new_role_id = update_data.pop("role_id", None)

    if "name" in update_data and update_data["name"] is not None:
        admin.name = update_data["name"]

    if new_role_id is not None:
        new_role = await _resolve_platform_role(db, new_role_id)

        if new_role.name != "SUPER_ADMIN":
            await _assert_not_last_super_admin(
                db, admin.id, reason="demote this admin from SUPER_ADMIN"
            )

        existing_rbac = (
            await db.execute(select(AdminUserRole).where(AdminUserRole.admin_user_id == admin.id))
        ).scalar_one_or_none()
        if existing_rbac:
            existing_rbac.role_id = new_role.id
            existing_rbac.assigned_by = auth.user_id
        else:
            db.add(
                AdminUserRole(
                    admin_user_id=admin.id,
                    role_id=new_role.id,
                    assigned_by=auth.user_id,
                )
            )

    await db.flush()
    await db.refresh(admin)
    return await _detail_response(db, admin)


@router.post("/{admin_id}/suspend", response_model=SuccessResponse)
async def suspend_admin(
    admin_id: UUID,
    auth: Annotated[AuthContext, Depends(require_permission("admin.admins.suspend"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SuccessResponse:
    admin = (
        await db.execute(select(AdminUser).where(AdminUser.id == admin_id))
    ).scalar_one_or_none()
    if admin is None:
        raise NotFoundError("Admin", str(admin_id))
    if str(admin.id) == str(auth.user_id):
        raise ConflictError("Cannot suspend yourself")
    if admin.status != AdminUserStatus.ACTIVE:
        raise ConflictError(f"Can only suspend active admins, current: '{admin.status.value}'")

    await _assert_not_last_super_admin(db, admin.id, reason="suspend this admin")

    admin.status = AdminUserStatus.DEACTIVATED
    await db.flush()
    return SuccessResponse(message=f"Admin '{admin.name}' suspended")


@router.post("/{admin_id}/activate", response_model=SuccessResponse)
async def activate_admin(
    admin_id: UUID,
    auth: Annotated[AuthContext, Depends(require_permission("admin.admins.suspend"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SuccessResponse:
    admin = (
        await db.execute(select(AdminUser).where(AdminUser.id == admin_id))
    ).scalar_one_or_none()
    if admin is None:
        raise NotFoundError("Admin", str(admin_id))
    if admin.status == AdminUserStatus.ACTIVE:
        raise ConflictError("Admin is already active")

    admin.status = AdminUserStatus.ACTIVE
    await db.flush()
    return SuccessResponse(message=f"Admin '{admin.name}' activated")


@router.delete("/{admin_id}", response_model=SuccessResponse)
async def delete_admin(
    admin_id: UUID,
    auth: Annotated[AuthContext, Depends(require_permission("admin.admins.delete"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SuccessResponse:
    """Soft-delete an admin (sets status=DEACTIVATED, removes RBAC role).

    The row itself is retained to preserve historical audit links. To fully
    restore, an invite flow is required.
    """
    admin = (
        await db.execute(select(AdminUser).where(AdminUser.id == admin_id))
    ).scalar_one_or_none()
    if admin is None:
        raise NotFoundError("Admin", str(admin_id))
    if str(admin.id) == str(auth.user_id):
        raise ConflictError("Cannot remove yourself")

    await _assert_not_last_super_admin(db, admin.id, reason="remove this admin")

    admin.status = AdminUserStatus.DEACTIVATED

    # Detach RBAC role so listing shows "no role" for removed admins.
    existing_rbac = (
        await db.execute(select(AdminUserRole).where(AdminUserRole.admin_user_id == admin.id))
    ).scalar_one_or_none()
    if existing_rbac:
        await db.delete(existing_rbac)

    await db.flush()
    return SuccessResponse(message=f"Admin '{admin.name}' removed")
