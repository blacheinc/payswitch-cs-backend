"""Shared FastAPI dependencies – authentication, tenant resolution, rate limiting."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    OrganizationSuspendedError,
)
from app.core.rate_limiter import RateLimiter
from app.core.security import decode_token, hash_api_key
from app.db import get_db
from app.models.api_key import ApiKey, ApiKeyStatus
from app.models.organization import OrgStatus, Organization
from app.models.user import AdminUser, AdminUserStatus, OrgUser, OrgUserStatus
from app.storage.cache import get_redis_cache

# ── HTTP Bearer (optional – used for portal JWT auth) ─────────────────────────

optional_bearer = HTTPBearer(auto_error=False)


# ── Data classes for resolved auth context ────────────────────────────────────


class AuthContext:
    """Resolved authentication context for a request."""

    def __init__(
        self,
        user_id: UUID | None = None,
        user_email: str | None = None,
        user_type: str = "unknown",  # "admin", "org_user", "api_key"
        organization_id: UUID | None = None,
        organization: Organization | None = None,
        api_key: ApiKey | None = None,
        role: str | None = None,
    ):
        self.user_id = user_id
        self.user_email = user_email
        self.user_type = user_type
        self.organization_id = organization_id
        self.organization = organization
        self.api_key = api_key
        self.role = role


# ── Admin Authentication ──────────────────────────────────────────────────────


async def get_current_admin(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(optional_bearer)
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AdminUser:
    """Authenticate a platform admin via JWT."""
    if credentials is None:
        raise AuthenticationError("Authorization header required")

    try:
        payload = decode_token(credentials.credentials)
    except Exception:
        raise AuthenticationError("Invalid or expired token")

    if payload.get("type") != "access" or payload.get("user_type") != "admin":
        raise AuthenticationError("Invalid token type")

    user_id = payload.get("sub")
    if not user_id:
        raise AuthenticationError("Invalid token payload")

    result = await db.execute(select(AdminUser).where(AdminUser.id == user_id))
    user = result.scalar_one_or_none()

    if user is None or user.status != AdminUserStatus.ACTIVE:
        raise AuthenticationError("User not found or deactivated")

    return user


async def require_super_admin(
    admin: Annotated[AdminUser, Depends(get_current_admin)],
) -> AdminUser:
    """Require super_admin role."""
    from app.models.user import AdminRole

    if admin.role != AdminRole.SUPER_ADMIN:
        raise AuthorizationError("Super admin access required")
    return admin


# ── Organization User Authentication (JWT) ───────────────────────────────────


async def get_current_org_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(optional_bearer)
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> OrgUser:
    """Authenticate an organization user via JWT."""
    if credentials is None:
        raise AuthenticationError("Authorization header required")

    try:
        payload = decode_token(credentials.credentials)
    except Exception:
        raise AuthenticationError("Invalid or expired token")

    if payload.get("type") != "access" or payload.get("user_type") != "org_user":
        raise AuthenticationError("Invalid token type")

    user_id = payload.get("sub")
    if not user_id:
        raise AuthenticationError("Invalid token payload")

    result = await db.execute(select(OrgUser).where(OrgUser.id == user_id).options())
    user = result.scalar_one_or_none()

    if user is None or user.status != OrgUserStatus.ACTIVE:
        raise AuthenticationError("User not found or inactive")

    # Check org is active
    org_result = await db.execute(
        select(Organization).where(Organization.id == user.organization_id)
    )
    org = org_result.scalar_one_or_none()
    if org is None or org.status == OrgStatus.SUSPENDED:
        raise OrganizationSuspendedError()

    return user


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(optional_bearer)
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AdminUser | OrgUser:
    """Authenticate either an admin or org user via JWT."""
    if credentials is None:
        raise AuthenticationError("Authorization header required")

    try:
        payload = decode_token(credentials.credentials)
    except Exception:
        raise AuthenticationError("Invalid or expired token")

    if payload.get("type") != "access":
        raise AuthenticationError("Invalid token type")

    user_type = payload.get("user_type")
    user_id = payload.get("sub")
    if not user_id:
        raise AuthenticationError("Invalid token payload")

    if user_type == "admin":
        result = await db.execute(select(AdminUser).where(AdminUser.id == user_id))
        user = result.scalar_one_or_none()
        if user is None or user.status != AdminUserStatus.ACTIVE:
            raise AuthenticationError("User not found or deactivated")
        return user

    if user_type == "org_user":
        result = await db.execute(
            select(OrgUser).where(OrgUser.id == user_id).options()
        )
        user = result.scalar_one_or_none()
        if user is None or user.status != OrgUserStatus.ACTIVE:
            raise AuthenticationError("User not found or inactive")

        org_result = await db.execute(
            select(Organization).where(Organization.id == user.organization_id)
        )
        org = org_result.scalar_one_or_none()
        if org is None or org.status == OrgStatus.SUSPENDED:
            raise OrganizationSuspendedError()
        return user

    raise AuthenticationError("Invalid token type")


# ── Dual Auth (API Key OR JWT) – for /v1/* endpoints ─────────────────────────


async def get_auth_context(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(optional_bearer)
    ],
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    """Resolve authentication from Authorization: Bearer <token>.

    The bearer value can be either a JWT (dashboard login) or an API key
    (programmatic integration). JWT is tried first; if decode fails, the
    value is looked up as an API key.
    """
    if not credentials:
        raise AuthenticationError("Authorization header required")

    token = credentials.credentials

    # ── 1. Try JWT decode ────────────────────────────────────────────────
    try:
        payload = decode_token(token)
    except Exception:
        payload = None

    if payload and payload.get("type") == "access":
        user_type = payload.get("user_type")
        user_id = payload.get("sub")

        if user_type == "org_user":
            result = await db.execute(select(OrgUser).where(OrgUser.id == user_id))
            user = result.scalar_one_or_none()
            if user is None or user.status != OrgUserStatus.ACTIVE:
                raise AuthenticationError("User not found or inactive")

            org_result = await db.execute(
                select(Organization).where(Organization.id == user.organization_id)
            )
            org = org_result.scalar_one_or_none()
            if org is None or org.status == OrgStatus.SUSPENDED:
                raise OrganizationSuspendedError()

            return AuthContext(
                user_id=user.id,
                user_email=user.email,
                user_type="org_user",
                organization_id=org.id,
                organization=org,
                role=user.role_label.value if user.role_label else None,
            )

        elif user_type == "admin":
            result = await db.execute(select(AdminUser).where(AdminUser.id == user_id))
            admin = result.scalar_one_or_none()
            if admin is None or admin.status != AdminUserStatus.ACTIVE:
                raise AuthenticationError("Admin not found or deactivated")

            return AuthContext(
                user_id=admin.id,
                user_email=admin.email,
                user_type="admin",
                role=admin.role.value if admin.role else None,
            )

    # ── 2. Try as API key ────────────────────────────────────────────────
    key_hash = hash_api_key(token)
    result = await db.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
    api_key = result.scalar_one_or_none()

    if api_key is not None and api_key.status == ApiKeyStatus.ACTIVE:
        # Load organization
        org_result = await db.execute(
            select(Organization).where(Organization.id == api_key.organization_id)
        )
        org = org_result.scalar_one_or_none()
        if org is None or org.status == OrgStatus.SUSPENDED:
            raise OrganizationSuspendedError()
        if org.status != OrgStatus.ACTIVE:
            raise AuthenticationError("Organization is not active")

        # Update last_used_at
        from app.db.base import utcnow

        api_key.last_used_at = utcnow()

        return AuthContext(
            user_type="api_key",
            organization_id=org.id,
            organization=org,
            api_key=api_key,
        )

    raise AuthenticationError("Invalid or expired token / API key")


# ── Rate Limiting Dependency ──────────────────────────────────────────────────


async def check_rate_limit(
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    request: Request,
) -> AuthContext:
    """Enforce rate limits based on the resolved organization."""
    if auth.organization_id:
        redis_cache = get_redis_cache()
        client = await redis_cache.get_client()
        limiter = RateLimiter(client)
        rate_headers = await limiter.check_rate_limit(str(auth.organization_id))
        # Store headers for the response
        request.state.rate_limit_headers = rate_headers

    # Expose auth context on request.state for API logging middleware
    request.state.organization_id = auth.organization_id
    request.state.user_id = auth.user_id
    request.state.api_key_id = auth.api_key.id if auth.api_key else None
    request.state.api_key_prefix = auth.api_key.key_prefix if auth.api_key else None

    return auth
