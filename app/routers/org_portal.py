"""Organization portal routers – profile, users, API keys, webhooks."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.pagination import PaginatedResponse
from app.core.security import (
    generate_api_key,
    generate_email_verification_token,
    generate_temp_password,
    generate_webhook_secret,
    hash_password,
)
from app.db import get_db
from app.dependencies import AuthContext, require_permission
from app.models.api_key import ApiKey, ApiKeyEnvironment, ApiKeyStatus
from app.models.organization import Organization
from app.models.permission import OrgUserRole, Role
from app.models.user import OrgRoleLabel, OrgUser, OrgUserStatus
from app.models.webhook import WebhookConfig
from app.schemas.api_key import (
    ApiKeyCreatedResponse,
    ApiKeyResponse,
    CreateApiKeyRequest,
)
from app.schemas.common import SuccessResponse
from app.schemas.organization import OrganizationResponse, UpdateOrganizationRequest
from app.schemas.user import (
    InviteUserRequest,
    OrgUserListResponse,
    OrgUserResponse,
    UpdateUserRequest,
)
from app.schemas.webhook import (
    CreateWebhookRequest,
    UpdateWebhookRequest,
    WebhookResponse,
)
from app.services.email import email_service

# ── Organization Profile ──────────────────────────────────────────────────────

profile_router = APIRouter(prefix="/org/profile", tags=["Org Portal – Profile"])


@profile_router.get("", response_model=OrganizationResponse)
async def get_org_profile(
    auth: Annotated[AuthContext, Depends(require_permission("org.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> OrganizationResponse:
    result = await db.execute(
        select(Organization).where(Organization.id == auth.organization_id)
    )
    org = result.scalar_one()
    return OrganizationResponse.model_validate(org)


@profile_router.patch("", response_model=OrganizationResponse)
async def update_org_profile(
    body: UpdateOrganizationRequest,
    auth: Annotated[AuthContext, Depends(require_permission("org.update"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> OrganizationResponse:
    result = await db.execute(
        select(Organization).where(Organization.id == auth.organization_id)
    )
    org = result.scalar_one()
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(org, field, value)
    await db.flush()
    await db.refresh(org)
    return OrganizationResponse.model_validate(org)


# ── Organization Users ────────────────────────────────────────────────────────

users_router = APIRouter(prefix="/org/users", tags=["Org Portal – Users"])


async def _resolve_role_for_invite(
    db: AsyncSession, role_id: UUID | None, org_id
) -> Role:
    """Return the Role to assign when inviting a user.

    If role_id is provided it must exist, be org-scoped, and belong to the
    caller's org (or be the system ADMIN role).  When omitted the system ADMIN
    role is used.
    """
    if role_id is not None:
        role = (
            await db.execute(select(Role).where(Role.id == role_id))
        ).scalar_one_or_none()
        if not role:
            raise NotFoundError("Role", str(role_id))
        if role.scope != "org":
            raise ValidationError("Only org-scoped roles can be assigned to org users")
        if not role.is_system and str(role.organization_id) != str(org_id):
            raise NotFoundError("Role", str(role_id))
        return role

    # Default to system ADMIN role
    admin_role = (
        await db.execute(
            select(Role).where(Role.name == "ADMIN", Role.is_system == True)  # noqa: E712
        )
    ).scalar_one_or_none()
    if not admin_role:
        raise ValidationError("System ADMIN role not found — ensure seeder has run")
    return admin_role


@users_router.post(
    "", response_model=OrgUserResponse, status_code=status.HTTP_201_CREATED
)
async def invite_user(
    body: InviteUserRequest,
    auth: Annotated[AuthContext, Depends(require_permission("users.invite"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> OrgUserResponse:
    """Invite a new user to the organization."""
    role = await _resolve_role_for_invite(db, body.role_id, auth.organization_id)

    existing = await db.execute(select(OrgUser).where(OrgUser.email == body.email))
    existing_user = existing.scalar_one_or_none()

    if existing_user:
        if existing_user.status != OrgUserStatus.REMOVED:
            raise ConflictError(f"User with email '{body.email}' already exists")

        # Re-activate previously removed user
        temp_password = generate_temp_password()
        existing_user.organization_id = auth.organization_id
        existing_user.name = body.name
        existing_user.password_hash = hash_password(temp_password)
        existing_user.role_label = OrgRoleLabel(body.role_label)
        existing_user.status = OrgUserStatus.ACTIVE
        existing_user.email_verification_token = generate_email_verification_token()
        existing_user.totp_secret = None
        existing_user.totp_enabled = False
        await db.flush()

        # Assign RBAC role (replace existing if any)
        existing_rbac = (
            await db.execute(
                select(OrgUserRole).where(OrgUserRole.org_user_id == existing_user.id)
            )
        ).scalar_one_or_none()
        if existing_rbac:
            existing_rbac.role_id = role.id
            existing_rbac.assigned_by = auth.user_id
        else:
            db.add(OrgUserRole(
                org_user_id=existing_user.id,
                role_id=role.id,
                assigned_by=auth.user_id,
            ))
        await db.flush()

        email_service.send_invite_email(
            to_email=existing_user.email,
            temp_password=temp_password,
            callback_url=body.callback_url,
        )
        return OrgUserResponse.model_validate(existing_user)

    temp_password = generate_temp_password()
    new_user = OrgUser(
        organization_id=auth.organization_id,
        email=body.email,
        name=body.name,
        password_hash=hash_password(temp_password),
        role_label=OrgRoleLabel(body.role_label),
        status=OrgUserStatus.ACTIVE,
        email_verification_token=generate_email_verification_token(),
    )
    db.add(new_user)
    await db.flush()

    db.add(OrgUserRole(
        org_user_id=new_user.id,
        role_id=role.id,
        assigned_by=auth.user_id,
    ))
    await db.flush()

    email_service.send_invite_email(
        to_email=new_user.email,
        temp_password=temp_password,
        callback_url=body.callback_url,
    )
    return OrgUserResponse.model_validate(new_user)


@users_router.get("", response_model=OrgUserListResponse)
async def list_users(
    auth: Annotated[AuthContext, Depends(require_permission("users.list"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
) -> OrgUserListResponse:
    # 1. Total members (exclude REMOVED)
    total_members = (
        await db.execute(
            select(func.count(OrgUser.id)).where(
                OrgUser.organization_id == auth.organization_id,
                OrgUser.status != OrgUserStatus.REMOVED,
            )
        )
    ).scalar_one()

    # 2. Pending invites
    pending_invites = (
        await db.execute(
            select(func.count(OrgUser.id)).where(
                OrgUser.organization_id == auth.organization_id,
                OrgUser.status == OrgUserStatus.PENDING,
            )
        )
    ).scalar_one()

    # 3. Admin users (exclude REMOVED)
    admin_users = (
        await db.execute(
            select(func.count(OrgUser.id)).where(
                OrgUser.organization_id == auth.organization_id,
                OrgUser.status != OrgUserStatus.REMOVED,
                OrgUser.role_label == OrgRoleLabel.ADMIN,
            )
        )
    ).scalar_one()

    result = await db.execute(
        select(OrgUser)
        .where(
            OrgUser.organization_id == auth.organization_id,
            OrgUser.status != OrgUserStatus.REMOVED,
        )
        .order_by(OrgUser.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    users = result.scalars().all()

    return OrgUserListResponse(
        items=[OrgUserResponse.model_validate(u) for u in users],
        total=total_members,
        page=page,
        per_page=per_page,
        total_pages=(total_members + per_page - 1) // per_page if per_page > 0 else 0,
        total_members=total_members,
        pending_invites=pending_invites,
        admin_users=admin_users,
    )


@users_router.delete("/{user_id}", response_model=SuccessResponse)
async def remove_user(
    user_id: UUID,
    auth: Annotated[AuthContext, Depends(require_permission("users.delete"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SuccessResponse:
    """Remove a user from the organization."""
    result = await db.execute(
        select(OrgUser).where(
            OrgUser.id == user_id,
            OrgUser.organization_id == auth.organization_id,
        )
    )
    target = result.scalar_one_or_none()
    if not target:
        raise NotFoundError("User", str(user_id))
    target.status = OrgUserStatus.REMOVED
    await db.flush()
    return SuccessResponse(message="User removed")


@users_router.patch("/{user_id}", response_model=OrgUserResponse)
async def update_user(
    user_id: UUID,
    body: UpdateUserRequest,
    auth: Annotated[AuthContext, Depends(require_permission("users.update"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> OrgUserResponse:
    """Update an organization user's name, role label, or RBAC role."""
    result = await db.execute(
        select(OrgUser).where(
            OrgUser.id == user_id,
            OrgUser.organization_id == auth.organization_id,
        )
    )
    target = result.scalar_one_or_none()
    if not target:
        raise NotFoundError("User", str(user_id))

    update_data = body.model_dump(exclude_unset=True)

    # Handle role_id update separately (RBAC assignment)
    new_role_id = update_data.pop("role_id", None)

    if "role_label" in update_data and update_data["role_label"] is not None:
        update_data["role_label"] = OrgRoleLabel(update_data["role_label"])

    for field, value in update_data.items():
        setattr(target, field, value)

    if new_role_id is not None:
        role = await _resolve_role_for_invite(db, new_role_id, auth.organization_id)
        existing_rbac = (
            await db.execute(
                select(OrgUserRole).where(OrgUserRole.org_user_id == target.id)
            )
        ).scalar_one_or_none()
        if existing_rbac:
            existing_rbac.role_id = role.id
            existing_rbac.assigned_by = auth.user_id
        else:
            db.add(OrgUserRole(
                org_user_id=target.id,
                role_id=role.id,
                assigned_by=auth.user_id,
            ))

    await db.flush()
    await db.refresh(target)
    return OrgUserResponse.model_validate(target)


@users_router.post("/{user_id}/suspend", response_model=SuccessResponse)
async def suspend_user(
    user_id: UUID,
    auth: Annotated[AuthContext, Depends(require_permission("users.suspend"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SuccessResponse:
    """Suspend an organization user."""
    result = await db.execute(
        select(OrgUser).where(
            OrgUser.id == user_id,
            OrgUser.organization_id == auth.organization_id,
        )
    )
    target = result.scalar_one_or_none()
    if not target:
        raise NotFoundError("User", str(user_id))
    if target.id == auth.user_id:
        raise ConflictError("Cannot suspend yourself")
    if target.status != OrgUserStatus.ACTIVE:
        raise ConflictError(
            f"Can only suspend active users, current: '{target.status.value}'"
        )

    target.status = OrgUserStatus.SUSPENDED
    await db.flush()
    return SuccessResponse(message=f"User '{target.name}' suspended")


@users_router.post("/{user_id}/activate", response_model=SuccessResponse)
async def activate_user(
    user_id: UUID,
    auth: Annotated[AuthContext, Depends(require_permission("users.suspend"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SuccessResponse:
    """Re-activate a suspended organization user."""
    result = await db.execute(
        select(OrgUser).where(
            OrgUser.id == user_id,
            OrgUser.organization_id == auth.organization_id,
        )
    )
    target = result.scalar_one_or_none()
    if not target:
        raise NotFoundError("User", str(user_id))
    if target.status != OrgUserStatus.SUSPENDED:
        raise ConflictError("User is not suspended")

    target.status = OrgUserStatus.ACTIVE
    await db.flush()
    return SuccessResponse(message=f"User '{target.name}' activated")


# ── API Keys ─────────────────────────────────────────────────────────────────

api_keys_router = APIRouter(prefix="/org/api-keys", tags=["Org Portal – API Keys"])


@api_keys_router.post(
    "", response_model=ApiKeyCreatedResponse, status_code=status.HTTP_201_CREATED
)
async def create_api_key(
    body: CreateApiKeyRequest,
    auth: Annotated[AuthContext, Depends(require_permission("api_keys.create"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ApiKeyCreatedResponse:
    """Generate a new API key."""
    # Resolve the role for this API key
    role = await _resolve_role_for_invite(db, body.role_id, auth.organization_id)

    full_key, prefix, key_hash = generate_api_key(body.environment)
    api_key = ApiKey(
        organization_id=auth.organization_id,
        key_hash=key_hash,
        key_prefix=prefix,
        name=body.name,
        environment=ApiKeyEnvironment(body.environment),
        created_by_user_id=auth.user_id,
        role_id=role.id,
    )
    db.add(api_key)
    await db.flush()
    return ApiKeyCreatedResponse(
        id=api_key.id,
        key=full_key,
        key_prefix=prefix,
        name=body.name,
        environment=body.environment,
    )


@api_keys_router.get("", response_model=list[ApiKeyResponse])
async def list_api_keys(
    auth: Annotated[AuthContext, Depends(require_permission("api_keys.list"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[ApiKeyResponse]:
    result = await db.execute(
        select(ApiKey).where(
            ApiKey.organization_id == auth.organization_id,
            ApiKey.status == ApiKeyStatus.ACTIVE,
        )
    )
    keys = result.scalars().all()
    return [ApiKeyResponse.model_validate(k) for k in keys]


@api_keys_router.delete("/{key_id}", response_model=SuccessResponse)
async def revoke_api_key(
    key_id: UUID,
    auth: Annotated[AuthContext, Depends(require_permission("api_keys.revoke"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SuccessResponse:
    result = await db.execute(
        select(ApiKey).where(
            ApiKey.id == key_id,
            ApiKey.organization_id == auth.organization_id,
        )
    )
    key = result.scalar_one_or_none()
    if not key:
        raise NotFoundError("API Key", str(key_id))
    from app.db.base import utcnow

    key.status = ApiKeyStatus.REVOKED
    key.revoked_at = utcnow()
    return SuccessResponse(message="API key revoked")


# ── Webhooks ──────────────────────────────────────────────────────────────────

webhooks_router = APIRouter(prefix="/org/webhooks", tags=["Org Portal – Webhooks"])

WEBHOOK_EVENTS = [
    {
        "event": "score.initiated",
        "description": "Fired when a new credit score request is received and processing begins.",
    },
    {
        "event": "score.completed",
        "description": "Fired when a credit score has been successfully computed and is ready.",
    },
    {
        "event": "score.failed",
        "description": "Fired when a credit score request fails during processing.",
    },
]


@webhooks_router.get("/events", response_model=list[dict])
async def list_webhook_events() -> list[dict]:
    """Return all supported webhook event types."""
    return WEBHOOK_EVENTS


@webhooks_router.post(
    "", response_model=WebhookResponse, status_code=status.HTTP_201_CREATED
)
async def create_webhook(
    body: CreateWebhookRequest,
    auth: Annotated[AuthContext, Depends(require_permission("webhooks.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> WebhookResponse:
    webhook = WebhookConfig(
        organization_id=auth.organization_id,
        url=body.url,
        events=body.events,
        description=body.description,
    )
    db.add(webhook)
    await db.flush()
    return WebhookResponse.model_validate(webhook)


@webhooks_router.get("", response_model=list[WebhookResponse])
async def list_webhooks(
    auth: Annotated[AuthContext, Depends(require_permission("webhooks.list"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[WebhookResponse]:
    result = await db.execute(
        select(WebhookConfig).where(
            WebhookConfig.organization_id == auth.organization_id
        )
    )
    configs = result.scalars().all()
    return [WebhookResponse.model_validate(c) for c in configs]


@webhooks_router.patch("/{webhook_id}", response_model=WebhookResponse)
async def update_webhook(
    webhook_id: UUID,
    body: UpdateWebhookRequest,
    auth: Annotated[AuthContext, Depends(require_permission("webhooks.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> WebhookResponse:
    result = await db.execute(
        select(WebhookConfig).where(
            WebhookConfig.id == webhook_id,
            WebhookConfig.organization_id == auth.organization_id,
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise NotFoundError("Webhook", str(webhook_id))
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(config, field, value)
    await db.flush()
    return WebhookResponse.model_validate(config)


@webhooks_router.delete("/{webhook_id}", response_model=SuccessResponse)
async def delete_webhook(
    webhook_id: UUID,
    auth: Annotated[AuthContext, Depends(require_permission("webhooks.manage"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SuccessResponse:
    result = await db.execute(
        select(WebhookConfig).where(
            WebhookConfig.id == webhook_id,
            WebhookConfig.organization_id == auth.organization_id,
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise NotFoundError("Webhook", str(webhook_id))
    await db.delete(config)
    return SuccessResponse(message="Webhook deleted")


# ── API Logs ──────────────────────────────────────────────────────────────────

api_logs_router = APIRouter(prefix="/org/api-logs", tags=["Org Portal – API Logs"])


@api_logs_router.get("", response_model=PaginatedResponse)
async def list_api_logs(
    auth: Annotated[AuthContext, Depends(require_permission("api_logs.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    method: str | None = Query(default=None, description="Filter by HTTP method"),
    status_code: int | None = Query(default=None, description="Exact status code"),
    status_class: str | None = Query(
        default=None,
        pattern="^[1-5]xx$",
        description="Status class filter, e.g. 4xx or 5xx",
    ),
    path: str | None = Query(default=None, description="Partial path match"),
    api_key_prefix: str | None = Query(
        default=None, description="Filter by API key prefix"
    ),
    from_date: str | None = Query(
        default=None, description="ISO date, e.g. 2026-01-01"
    ),
    to_date: str | None = Query(default=None, description="ISO date, e.g. 2026-02-01"),
):
    """Paginated list of API request logs for the authenticated organisation."""
    from datetime import datetime as dt

    from app.models.api_log import ApiLog

    query = select(ApiLog).where(ApiLog.organization_id == auth.organization_id)
    count_query = (
        select(func.count())
        .select_from(ApiLog)
        .where(ApiLog.organization_id == auth.organization_id)
    )

    # Filters
    if method:
        query = query.where(ApiLog.method == method.upper())
        count_query = count_query.where(ApiLog.method == method.upper())
    if status_code is not None:
        query = query.where(ApiLog.status_code == status_code)
        count_query = count_query.where(ApiLog.status_code == status_code)
    if status_class:
        base = int(status_class[0]) * 100
        query = query.where(ApiLog.status_code >= base, ApiLog.status_code < base + 100)
        count_query = count_query.where(
            ApiLog.status_code >= base, ApiLog.status_code < base + 100
        )
    if path:
        query = query.where(ApiLog.path.ilike(f"%{path}%"))
        count_query = count_query.where(ApiLog.path.ilike(f"%{path}%"))
    if api_key_prefix:
        query = query.where(ApiLog.api_key_prefix == api_key_prefix)
        count_query = count_query.where(ApiLog.api_key_prefix == api_key_prefix)
    if from_date:
        start = dt.fromisoformat(from_date)
        query = query.where(ApiLog.created_at >= start)
        count_query = count_query.where(ApiLog.created_at >= start)
    if to_date:
        end = dt.fromisoformat(to_date)
        query = query.where(ApiLog.created_at <= end)
        count_query = count_query.where(ApiLog.created_at <= end)

    # Count
    total = (await db.execute(count_query)).scalar() or 0

    # Fetch page
    offset = (page - 1) * per_page
    query = query.order_by(ApiLog.created_at.desc()).offset(offset).limit(per_page)
    result = await db.execute(query)
    logs = result.scalars().all()

    from app.schemas.api_log import ApiLogResponse
    from app.services.api_log_enrichment import pick_actor, resolve_actors

    actors = await resolve_actors(db, logs)

    return PaginatedResponse.create(
        items=[
            ApiLogResponse.model_validate(log).model_copy(
                update={"actor": pick_actor(log, actors)}
            )
            for log in logs
        ],
        total=total,
        page=page,
        per_page=per_page,
    )
