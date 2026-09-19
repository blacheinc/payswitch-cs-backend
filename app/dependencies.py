"""Shared FastAPI dependencies – authentication, tenant resolution, rate limiting."""

from __future__ import annotations

import logging
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
    RateLimitError,
)
from app.core.rate_limiter import RateLimiter
from app.core.security import decode_token, hash_api_key
from app.db import get_db
from app.models.api_key import ApiKey, ApiKeyStatus
from app.models.organization import Organization, OrgStatus
from app.models.user import AdminUser, AdminUserStatus, OrgUser, OrgUserStatus
from app.storage.cache import get_redis_cache

logger = logging.getLogger(__name__)

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
        # Populated by require_permission() on first access — None means not yet loaded.
        self.permissions: set[str] | None = None


# ── Admin Authentication ──────────────────────────────────────────────────────


async def get_current_admin(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(optional_bearer)],
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


# ── Organization User Authentication (JWT) ───────────────────────────────────


async def get_current_org_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(optional_bearer)],
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
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(optional_bearer)],
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

    # A token minted for an account that still holds a system-generated password
    # is scoped to the change-password flow only. Rejecting it here covers every
    # endpoint at once, so the emailed credential cannot reach real functionality
    # (VAPT finding 6). /auth/change-password uses get_user_pending_password_change.
    if payload.get("pwd_change_required"):
        raise AuthenticationError("Password change required before continuing")

    user_type = payload.get("user_type")
    user_id = payload.get("sub")
    if not user_id:
        raise AuthenticationError("Invalid token payload")

    if user_type == "admin":
        admin_result = await db.execute(select(AdminUser).where(AdminUser.id == user_id))
        admin = admin_result.scalar_one_or_none()
        if admin is None or admin.status != AdminUserStatus.ACTIVE:
            raise AuthenticationError("User not found or deactivated")
        return admin

    if user_type == "org_user":
        org_user_result = await db.execute(select(OrgUser).where(OrgUser.id == user_id))
        org_user = org_user_result.scalar_one_or_none()
        if org_user is None or org_user.status != OrgUserStatus.ACTIVE:
            raise AuthenticationError("User not found or inactive")

        org_result = await db.execute(
            select(Organization).where(Organization.id == org_user.organization_id)
        )
        org = org_result.scalar_one_or_none()
        if org is None or org.status == OrgStatus.SUSPENDED:
            raise OrganizationSuspendedError()
        return org_user

    raise AuthenticationError("Invalid token type")


# ── Dual Auth (API Key OR JWT) – for /v1/* endpoints ─────────────────────────


async def get_auth_context(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(optional_bearer)],
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
            org_user_result = await db.execute(select(OrgUser).where(OrgUser.id == user_id))
            org_user = org_user_result.scalar_one_or_none()
            if org_user is None or org_user.status != OrgUserStatus.ACTIVE:
                raise AuthenticationError("User not found or inactive")

            org_result = await db.execute(
                select(Organization).where(Organization.id == org_user.organization_id)
            )
            org = org_result.scalar_one_or_none()
            if org is None or org.status == OrgStatus.SUSPENDED:
                raise OrganizationSuspendedError()

            return AuthContext(
                user_id=org_user.id,
                user_email=org_user.email,
                user_type="org_user",
                organization_id=org.id,
                organization=org,
                role=org_user.role_label.value if org_user.role_label else None,
            )

        elif user_type == "admin":
            admin_result = await db.execute(select(AdminUser).where(AdminUser.id == user_id))
            admin = admin_result.scalar_one_or_none()
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
    api_key_result = await db.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
    api_key = api_key_result.scalar_one_or_none()

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



async def get_user_pending_password_change(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(optional_bearer)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AdminUser | OrgUser:
    """Resolve a user from a normal *or* a password-change-scoped token.

    Only /auth/change-password uses this. It is the one route that must remain
    reachable while ``must_change_password`` is set, otherwise a flagged account
    has no way to clear the flag.
    """
    if credentials is None:
        raise AuthenticationError("Authorization header required")

    try:
        payload = decode_token(credentials.credentials)
    except Exception:
        raise AuthenticationError("Invalid or expired token")

    if payload.get("type") != "access":
        raise AuthenticationError("Invalid token type")

    user_id = payload.get("sub")
    if not user_id:
        raise AuthenticationError("Invalid token payload")

    if payload.get("user_type") == "admin":
        result = await db.execute(select(AdminUser).where(AdminUser.id == user_id))
        admin = result.scalar_one_or_none()
        if admin is None or admin.status != AdminUserStatus.ACTIVE:
            raise AuthenticationError("User not found or deactivated")
        return admin

    result = await db.execute(select(OrgUser).where(OrgUser.id == user_id))
    org_user = result.scalar_one_or_none()
    if org_user is None or org_user.status != OrgUserStatus.ACTIVE:
        raise AuthenticationError("User not found or deactivated")
    return org_user


# ── Rate Limiting Dependency ──────────────────────────────────────────────────


async def check_rate_limit(
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    request: Request,
) -> AuthContext:
    """Enforce rate limits based on the resolved organization.

    Fails OPEN: if Redis is unavailable, the request is allowed rather than
    500'd. A real limit breach (``RateLimitError``) still propagates as 429.
    """
    if auth.organization_id:
        try:
            redis_cache = get_redis_cache()
            client = await redis_cache.get_client()
            limiter = RateLimiter(client)
            rate_headers = await limiter.check_rate_limit(str(auth.organization_id))
            # Store headers for the response
            request.state.rate_limit_headers = rate_headers
        except RateLimitError:
            raise
        except Exception as e:  # Redis down/slow — don't take the API down with it
            logger.warning(
                "Rate limiter unavailable (%s: %s) — allowing request (fail-open)",
                type(e).__name__,
                e,
            )

    # Expose auth context on request.state for API logging middleware
    request.state.organization_id = auth.organization_id
    request.state.user_id = auth.user_id
    request.state.api_key_id = auth.api_key.id if auth.api_key else None
    request.state.api_key_prefix = auth.api_key.key_prefix if auth.api_key else None

    return auth


# ── Permission-gated dependency factory ──────────────────────────────────────


def require_any_permission(*codes: str):
    """Like require_permission but accepts if the caller has ANY of the given codes.

    Useful for endpoints reachable by both admin and org users where the
    permission code differs (e.g. "admin.roles.read" vs "roles.read").
    """

    async def _check(
        auth: Annotated[AuthContext, Depends(check_rate_limit)],
        db: Annotated[AsyncSession, Depends(get_db)],
    ) -> AuthContext:
        from app.services.permissions import load_user_permissions

        permissions = await load_user_permissions(db, auth)
        auth.permissions = permissions

        if "*" not in permissions and not any(c in permissions for c in codes):
            raise AuthorizationError(f"One of these permissions required: {', '.join(codes)}")

        return auth

    return _check


def require_permission(code: str):
    """Return a FastAPI dependency that enforces *code* permission.

    Chains on top of check_rate_limit (which itself chains on get_auth_context),
    so rate-limiting is always applied before the permission check.

    Usage::

        @router.post("/something")
        async def handler(
            auth: Annotated[
                AuthContext,
                Depends(require_permission("score_requests.create")),
            ],
        ):
            ...
    """

    async def _check(
        auth: Annotated[AuthContext, Depends(check_rate_limit)],
        db: Annotated[AsyncSession, Depends(get_db)],
    ) -> AuthContext:
        from app.services.permissions import load_user_permissions

        permissions = await load_user_permissions(db, auth)
        auth.permissions = permissions

        if "*" not in permissions and code not in permissions:
            raise AuthorizationError(f"Permission required: {code}")

        return auth

    return _check
