"""Admin Organization Management router."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.pagination import PaginatedResponse
from app.core.security import (
    generate_api_key,
    generate_temp_password,
    generate_webhook_secret,
    hash_password,
    generate_email_verification_token,
)
from app.db import get_db
from app.dependencies import AuthContext, require_permission
from app.models.api_key import ApiKey, ApiKeyEnvironment
from app.models.organization import IndustryType, OrgStatus, Organization
from app.models.permission import OrgUserRole, Role
from app.models.user import OrgRoleLabel, OrgUser, OrgUserStatus
from app.schemas.common import SuccessResponse
from app.schemas.organization import (
    CreateOrganizationRequest,
    OrganizationResponse,
    ProvisionOrganizationRequest,
    ProvisionResponse,
    SuspendRequest,
    UpdateOrganizationRequest,
)
from app.schemas.user import OrgUserResponse, UpdateUserRequest
from app.services.email import email_service

router = APIRouter(prefix="/admin/organizations", tags=["Admin – Organizations"])


@router.post(
    "",
    response_model=OrganizationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_organization(
    body: CreateOrganizationRequest,
    _auth: Annotated[AuthContext, Depends(require_permission("admin.organizations.create"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> OrganizationResponse:
    """Create a new organization (admin only)."""
    # Check unique short_name
    existing = await db.execute(
        select(Organization).where(Organization.short_name == body.short_name)
    )
    if existing.scalar_one_or_none():
        raise ConflictError(
            f"Organization with short_name '{body.short_name}' already exists"
        )

    # Check unique primary_contact_email
    if body.primary_contact_email:
        existing = await db.execute(
            select(Organization).where(
                Organization.primary_contact_email == body.primary_contact_email
            )
        )
        if existing.scalar_one_or_none():
            raise ConflictError(
                f"Organization with primary_contact_email '{body.primary_contact_email}' already exists"
            )

    org = Organization(
        name=body.name,
        short_name=body.short_name,
        industry_type=IndustryType(body.industry_type),
        primary_contact_name=body.primary_contact_name,
        primary_contact_email=body.primary_contact_email,
        primary_contact_phone=body.primary_contact_phone,
        address=body.address,
        webhook_secret=generate_webhook_secret(),
    )
    db.add(org)
    await db.flush()
    return OrganizationResponse.model_validate(org)


@router.get("", response_model=PaginatedResponse[OrganizationResponse])
async def list_organizations(
    _auth: Annotated[AuthContext, Depends(require_permission("admin.organizations.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    status_filter: str | None = Query(None, alias="status"),
    search: str | None = Query(None),
) -> PaginatedResponse[OrganizationResponse]:
    """List all organizations (paginated)."""
    query = select(Organization)
    count_query = select(func.count(Organization.id))

    if status_filter:
        query = query.where(Organization.status == OrgStatus(status_filter))
        count_query = count_query.where(Organization.status == OrgStatus(status_filter))
    if search:
        query = query.where(
            Organization.name.ilike(f"%{search}%")
            | Organization.short_name.ilike(f"%{search}%")
        )
        count_query = count_query.where(
            Organization.name.ilike(f"%{search}%")
            | Organization.short_name.ilike(f"%{search}%")
        )

    total = (await db.execute(count_query)).scalar_one()
    result = await db.execute(
        query.order_by(Organization.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    orgs = result.scalars().all()

    return PaginatedResponse.create(
        items=[OrganizationResponse.model_validate(o) for o in orgs],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/{org_id}", response_model=OrganizationResponse)
async def get_organization(
    org_id: UUID,
    _auth: Annotated[AuthContext, Depends(require_permission("admin.organizations.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> OrganizationResponse:
    """Get organization details."""
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one_or_none()
    if not org:
        raise NotFoundError("Organization", str(org_id))
    return OrganizationResponse.model_validate(org)


@router.patch("/{org_id}", response_model=OrganizationResponse)
async def update_organization(
    org_id: UUID,
    body: UpdateOrganizationRequest,
    _auth: Annotated[AuthContext, Depends(require_permission("admin.organizations.update"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> OrganizationResponse:
    """Update organization details."""
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one_or_none()
    if not org:
        raise NotFoundError("Organization", str(org_id))

    update_data = body.model_dump(exclude_unset=True)

    # Check unique primary_contact_email if being changed
    new_email = update_data.get("primary_contact_email")
    if new_email and new_email != org.primary_contact_email:
        existing = await db.execute(
            select(Organization).where(
                Organization.primary_contact_email == new_email,
                Organization.id != org_id,
            )
        )
        if existing.scalar_one_or_none():
            raise ConflictError(
                f"Organization with primary_contact_email '{new_email}' already exists"
            )

    for field, value in update_data.items():
        setattr(org, field, value)

    await db.flush()
    await db.refresh(org)
    return OrganizationResponse.model_validate(org)


@router.post("/{org_id}/provision", response_model=ProvisionResponse)
async def provision_organization(
    org_id: UUID,
    _auth: Annotated[AuthContext, Depends(require_permission("admin.organizations.provision"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    body: ProvisionOrganizationRequest | None = None,
) -> ProvisionResponse:
    """Provision org: activate, create admin user, generate API keys, send welcome email."""
    # If callback_url is provided in body, use it for the welcome email link
    callback_url = body.callback_url if body else None
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one_or_none()
    if not org:
        raise NotFoundError("Organization", str(org_id))

    if org.status == OrgStatus.ACTIVE:
        raise ConflictError("Organization already provisioned")

    # Activate
    org.status = OrgStatus.ACTIVE

    # Create org admin user — primary_contact_email is required because the
    # provisioning email (with the temp password) must reach a real mailbox
    # owned by the organization.
    if not org.primary_contact_email:
        raise ValidationError(
            "Organization has no primary_contact_email — cannot provision. "
            "Update the organization profile first."
        )

    # Look up the system ADMIN role — required for granting the org admin RBAC permissions
    admin_role_result = await db.execute(
        select(Role).where(Role.name == "ADMIN", Role.is_system == True)  # noqa: E712
    )
    admin_role = admin_role_result.scalar_one_or_none()
    if not admin_role:
        raise ValidationError(
            "System ADMIN role not found — ensure the permission seeder has run."
        )

    # Check whether the primary contact email is already an org user
    existing_user_result = await db.execute(
        select(OrgUser).where(OrgUser.email == org.primary_contact_email)
    )
    existing_org_user = existing_user_result.scalar_one_or_none()

    temp_password = generate_temp_password()

    if existing_org_user:
        if existing_org_user.status not in (OrgUserStatus.REMOVED, OrgUserStatus.PENDING):
            raise ConflictError(
                f"Email '{org.primary_contact_email}' is already associated with another "
                "organization. Update the organization's primary_contact_email to a unique "
                "address before provisioning."
            )
        # Re-use the existing row (removed or pending from a previous provision attempt)
        existing_org_user.organization_id = org.id
        existing_org_user.name = org.primary_contact_name or f"{org.name} Admin"
        existing_org_user.password_hash = hash_password(temp_password)
        existing_org_user.role_label = OrgRoleLabel.ADMIN
        existing_org_user.status = OrgUserStatus.ACTIVE
        existing_org_user.email_verification_token = generate_email_verification_token()
        existing_org_user.totp_secret = None
        existing_org_user.totp_enabled = False
        org_admin = existing_org_user
    else:
        org_admin = OrgUser(
            organization_id=org.id,
            email=org.primary_contact_email,
            name=org.primary_contact_name or f"{org.name} Admin",
            password_hash=hash_password(temp_password),
            role_label=OrgRoleLabel.ADMIN,
            status=OrgUserStatus.ACTIVE,
            email_verification_token=generate_email_verification_token(),
        )
        db.add(org_admin)

    # Generate API keys (sandbox + production)
    sandbox_full, sandbox_prefix, sandbox_hash = generate_api_key("sandbox")
    prod_full, prod_prefix, prod_hash = generate_api_key("production")

    sandbox_key = ApiKey(
        organization_id=org.id,
        key_hash=sandbox_hash,
        key_prefix=sandbox_prefix,
        name=f"{org.short_name}-sandbox",
        environment=ApiKeyEnvironment.SANDBOX,
    )
    prod_key = ApiKey(
        organization_id=org.id,
        key_hash=prod_hash,
        key_prefix=prod_prefix,
        name=f"{org.short_name}-production",
        environment=ApiKeyEnvironment.PRODUCTION,
    )
    db.add(sandbox_key)
    db.add(prod_key)

    try:
        await db.flush()
    except IntegrityError:
        raise ConflictError(
            f"Email '{org.primary_contact_email}' is already in use. "
            "Update the organization's primary_contact_email before provisioning."
        )

    # Assign the system ADMIN RBAC role so the user has full org-scoped permissions.
    # Re-use an existing assignment if the user was previously provisioned/removed.
    existing_rbac = (
        await db.execute(
            select(OrgUserRole).where(OrgUserRole.org_user_id == org_admin.id)
        )
    ).scalar_one_or_none()
    if existing_rbac:
        existing_rbac.role_id = admin_role.id
        existing_rbac.assigned_by = _auth.user_id
    else:
        db.add(
            OrgUserRole(
                org_user_id=org_admin.id,
                role_id=admin_role.id,
                assigned_by=_auth.user_id,
            )
        )
    await db.flush()

    await db.refresh(org)

    email_service.send_provision_email(
        to_email=org_admin.email,
        temp_password=temp_password,
        callback_url=callback_url,
    )

    return ProvisionResponse(
        organization=OrganizationResponse.model_validate(org),
        admin_email=org_admin.email,
        sandbox_api_key=sandbox_full,
        production_api_key=prod_full,
    )


@router.post("/{org_id}/suspend", response_model=SuccessResponse)
async def suspend_organization(
    org_id: UUID,
    body: SuspendRequest,
    _auth: Annotated[AuthContext, Depends(require_permission("admin.organizations.suspend"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SuccessResponse:
    """Suspend an organization (with reason)."""
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one_or_none()
    if not org:
        raise NotFoundError("Organization", str(org_id))

    if org.status != OrgStatus.ACTIVE:
        raise ConflictError("Organization is not active")

    org.status = OrgStatus.SUSPENDED
    org.suspension_reason = body.reason

    return SuccessResponse(message=f"Organization '{org.name}' suspended")


@router.post("/{org_id}/activate", response_model=SuccessResponse)
async def activate_organization(
    org_id: UUID,
    _auth: Annotated[AuthContext, Depends(require_permission("admin.organizations.suspend"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SuccessResponse:
    """Re-activate a suspended organization."""
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one_or_none()
    if not org:
        raise NotFoundError("Organization", str(org_id))

    if org.status != OrgStatus.SUSPENDED:
        raise ConflictError("Organization is not suspended")

    org.status = OrgStatus.ACTIVE
    org.suspension_reason = None

    return SuccessResponse(message=f"Organization '{org.name}' activated")


# ── Admin: Organization Users ────────────────────────────────────────────────


@router.get("/{org_id}/users", response_model=PaginatedResponse[OrgUserResponse])
async def list_org_users(
    org_id: UUID,
    _auth: Annotated[AuthContext, Depends(require_permission("admin.organizations.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    status_filter: str | None = Query(None, alias="status"),
) -> PaginatedResponse[OrgUserResponse]:
    """List users for an organization (admin only)."""
    # Verify org exists
    org_result = await db.execute(select(Organization).where(Organization.id == org_id))
    if not org_result.scalar_one_or_none():
        raise NotFoundError("Organization", str(org_id))

    query = select(OrgUser).where(OrgUser.organization_id == org_id)
    count_query = select(func.count(OrgUser.id)).where(
        OrgUser.organization_id == org_id
    )

    if status_filter:
        query = query.where(OrgUser.status == OrgUserStatus(status_filter))
        count_query = count_query.where(OrgUser.status == OrgUserStatus(status_filter))

    total = (await db.execute(count_query)).scalar_one()
    result = await db.execute(
        query.order_by(OrgUser.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    users = result.scalars().all()

    return PaginatedResponse.create(
        items=[OrgUserResponse.model_validate(u) for u in users],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.patch("/{org_id}/users/{user_id}", response_model=OrgUserResponse)
async def update_org_user(
    org_id: UUID,
    user_id: UUID,
    body: UpdateUserRequest,
    _auth: Annotated[AuthContext, Depends(require_permission("admin.organizations.update"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> OrgUserResponse:
    """Update an organization user's details (admin only)."""
    result = await db.execute(
        select(OrgUser).where(
            OrgUser.id == user_id,
            OrgUser.organization_id == org_id,
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundError("User", str(user_id))

    update_data = body.model_dump(exclude_unset=True)

    # Convert string enums to proper types
    if "role_label" in update_data and update_data["role_label"] is not None:
        update_data["role_label"] = OrgRoleLabel(update_data["role_label"])

    for field, value in update_data.items():
        setattr(user, field, value)

    await db.flush()
    await db.refresh(user)
    return OrgUserResponse.model_validate(user)
