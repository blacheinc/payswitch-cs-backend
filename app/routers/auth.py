"""Authentication router – login, refresh, password reset, 2FA."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from jose import jwt

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.exceptions import AuthenticationError, NotFoundError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_email_verification_token,
    generate_totp_secret,
    get_totp_uri,
    hash_password,
    verify_password,
    verify_totp,
)
from app.db import get_db
from app.db.base import utcnow
from app.dependencies import AuthContext, get_auth_context, get_current_admin, get_current_org_user, get_current_user
from app.models.user import AdminUser, AdminUserStatus, OrgUser, OrgUserStatus
from app.schemas.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    LoginResponse,
    RefreshTokenRequest,
    ResetPasswordRequest,
    Remove2FARequest,
    Setup2FAResponse,
    TokenResponse,
    UserPermissionsResponse,
    UserProfileResponse,
    Verify2FARequest,
    Verify2FAResponse,
    VerifyEmailRequest,
)
from app.schemas.common import SuccessResponse
from app.services.email import email_service

router = APIRouter(prefix="/auth", tags=["Authentication"])
settings = get_settings()


@router.get("/me", response_model=UserProfileResponse)
async def get_me(
    current_user: Annotated[AdminUser | OrgUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserProfileResponse:
    """Get the currently authenticated user's profile, including their resolved permissions."""
    from app.services.permissions import load_user_permissions

    if isinstance(current_user, AdminUser):
        ctx = AuthContext(
            user_id=current_user.id,
            user_email=current_user.email,
            user_type="admin",
        )
        perms = sorted(await load_user_permissions(db, ctx))
        return UserProfileResponse(
            id=str(current_user.id),
            email=current_user.email,
            name=current_user.name,
            user_type="admin",
            role=current_user.role.value,
            status=current_user.status.value,
            totp_enabled=current_user.totp_enabled,
            last_login_at=(
                current_user.last_login_at.isoformat()
                if current_user.last_login_at
                else None
            ),
            created_at=(
                current_user.created_at.isoformat()
                if current_user.created_at
                else None
            ),
            permissions=perms,
        )

    ctx = AuthContext(
        user_id=current_user.id,
        user_email=current_user.email,
        user_type="org_user",
        organization_id=current_user.organization_id,
    )
    perms = sorted(await load_user_permissions(db, ctx))
    return UserProfileResponse(
        id=str(current_user.id),
        email=current_user.email,
        name=current_user.name,
        user_type="org_user",
        role=current_user.role_label.value,
        status=current_user.status.value,
        totp_enabled=current_user.totp_enabled,
        organization_id=str(current_user.organization_id),
        last_login_at=(
            current_user.last_login_at.isoformat()
            if current_user.last_login_at
            else None
        ),
        created_at=(
            current_user.created_at.isoformat()
            if current_user.created_at
            else None
        ),
        permissions=perms,
    )


@router.get("/me/permissions", response_model=UserPermissionsResponse)
async def get_my_permissions(
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserPermissionsResponse:
    """Return the resolved permission set for the currently authenticated principal.

    Works for JWT-authenticated users (admin and org users) as well as API keys.
    Call this once after login and cache the result in your auth store — refresh
    whenever the user's role changes.

    A ``permissions`` list containing ``["*"]`` means the caller holds the
    SUPER_ADMIN role and passes every permission check on the platform.
    """
    from app.services.permissions import load_user_permissions

    perms = await load_user_permissions(db, auth)
    is_super = "*" in perms
    user_id = str(auth.user_id) if auth.user_id else (
        str(auth.api_key.id) if auth.api_key else "unknown"
    )
    return UserPermissionsResponse(
        user_id=user_id,
        user_type=auth.user_type,
        permissions=sorted(perms),
        is_super_admin=is_super,
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> LoginResponse:
    """Authenticate via email/password. Returns JWT tokens."""
    # Try admin first, then org user
    result = await db.execute(select(AdminUser).where(AdminUser.email == body.email))
    admin = result.scalar_one_or_none()

    if admin and admin.status == AdminUserStatus.ACTIVE:
        if not verify_password(body.password, admin.password_hash):
            raise AuthenticationError()

        admin.last_login_at = utcnow()

        admin_user_data = {
            "id": str(admin.id),
            "email": admin.email,
            "name": admin.name,
            "role": admin.role.value,
            "status": admin.status.value,
            "totp_enabled": admin.totp_enabled,
            "last_login_at": (
                admin.last_login_at.isoformat() if admin.last_login_at else None
            ),
            "created_at": admin.created_at.isoformat() if admin.created_at else None,
        }

        if admin.totp_enabled or admin.totp_secret:
            # Return partial token for 2FA completion
            temp_token = create_access_token(
                admin.id,
                extra_claims={"user_type": "admin", "2fa_pending": True},
            )
            return LoginResponse(
                access_token=temp_token,
                refresh_token="",
                expires_in=300,
                requires_2fa=True,
                user_type="admin",
                user=admin_user_data,
            )

        access_token = create_access_token(
            admin.id, extra_claims={"user_type": "admin", "role": admin.role.value}
        )
        refresh_token = create_refresh_token(admin.id)

        return LoginResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=settings.access_token_expire_minutes * 60,
            user_type="admin",
            user=admin_user_data,
        )

    # Try org user
    result = await db.execute(select(OrgUser).where(OrgUser.email == body.email))
    org_user = result.scalar_one_or_none()

    if org_user and org_user.status == OrgUserStatus.ACTIVE:
        if not verify_password(body.password, org_user.password_hash):
            raise AuthenticationError()

        org_user.last_login_at = utcnow()

        org_user_data = {
            "id": str(org_user.id),
            "email": org_user.email,
            "name": org_user.name,
            "organization_id": str(org_user.organization_id),
            "role_label": org_user.role_label.value,
            "status": org_user.status.value,
            "totp_enabled": org_user.totp_enabled,
            "last_login_at": (
                org_user.last_login_at.isoformat() if org_user.last_login_at else None
            ),
            "created_at": (
                org_user.created_at.isoformat() if org_user.created_at else None
            ),
        }

        if org_user.totp_enabled or org_user.totp_secret:
            temp_token = create_access_token(
                org_user.id,
                extra_claims={"user_type": "org_user", "2fa_pending": True},
            )
            return LoginResponse(
                access_token=temp_token,
                refresh_token="",
                expires_in=300,
                requires_2fa=True,
                user_type="org_user",
                user=org_user_data,
            )

        access_token = create_access_token(
            org_user.id,
            extra_claims={
                "user_type": "org_user",
                "org_id": str(org_user.organization_id),
                "role": org_user.role_label.value,
            },
        )
        refresh_token = create_refresh_token(org_user.id)

        return LoginResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=settings.access_token_expire_minutes * 60,
            user_type="org_user",
            user=org_user_data,
        )

    raise AuthenticationError()


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    body: RefreshTokenRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    """Exchange a refresh token for a new access token."""
    try:
        payload = decode_token(body.refresh_token)
    except Exception:
        raise AuthenticationError("Invalid refresh token")

    if payload.get("type") != "refresh":
        raise AuthenticationError("Invalid token type")

    user_id = payload.get("sub")

    # Look up user to get fresh claims
    result = await db.execute(select(AdminUser).where(AdminUser.id == user_id))
    admin = result.scalar_one_or_none()
    if admin and admin.status == AdminUserStatus.ACTIVE:
        access_token = create_access_token(
            admin.id, extra_claims={"user_type": "admin", "role": admin.role.value}
        )
        return TokenResponse(
            access_token=access_token,
            expires_in=settings.access_token_expire_minutes * 60,
        )

    result = await db.execute(select(OrgUser).where(OrgUser.id == user_id))
    org_user = result.scalar_one_or_none()
    if org_user and org_user.status == OrgUserStatus.ACTIVE:
        access_token = create_access_token(
            org_user.id,
            extra_claims={
                "user_type": "org_user",
                "org_id": str(org_user.organization_id),
                "role": org_user.role_label.value,
            },
        )
        return TokenResponse(
            access_token=access_token,
            expires_in=settings.access_token_expire_minutes * 60,
        )

    raise AuthenticationError("User not found or inactive")


@router.post("/logout", response_model=SuccessResponse)
async def logout() -> SuccessResponse:
    """Invalidate the current session (client should discard tokens)."""
    # In a full implementation, we'd blacklist the JWT jti in Redis.
    return SuccessResponse(message="Logged out successfully")


@router.post("/forgot-password", response_model=SuccessResponse)
async def forgot_password(
    body: ForgotPasswordRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SuccessResponse:
    """Request a password reset email. Token expires in 1 hour."""
    expiry = utcnow() + timedelta(hours=1)

    # Check org users first
    result = await db.execute(select(OrgUser).where(OrgUser.email == body.email))
    user = result.scalar_one_or_none()
    if user:
        user.password_reset_token = generate_email_verification_token()
        user.password_reset_expires = expiry
        email_service.send_password_reset_email(
            to_email=user.email,
            token=user.password_reset_token,
            callback_url=body.callback_url,
        )
    else:
        # Check admin users
        result = await db.execute(
            select(AdminUser).where(AdminUser.email == body.email)
        )
        admin = result.scalar_one_or_none()
        if admin:
            admin.password_reset_token = generate_email_verification_token()
            admin.password_reset_expires = expiry
            email_service.send_password_reset_email(
                to_email=admin.email,
                token=admin.password_reset_token,
                callback_url=body.callback_url,
            )
    # Always return success to prevent email enumeration
    return SuccessResponse(message="If that email exists, a reset link has been sent")


@router.post("/reset-password", response_model=SuccessResponse)
async def reset_password(
    body: ResetPasswordRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SuccessResponse:
    """Reset password using a reset token (must not be expired)."""
    now = utcnow()

    # Check org users first
    result = await db.execute(
        select(OrgUser).where(OrgUser.password_reset_token == body.token)
    )
    user = result.scalar_one_or_none()
    if user:
        if user.password_reset_expires and user.password_reset_expires < now:
            user.password_reset_token = None
            user.password_reset_expires = None
            raise AuthenticationError("Reset token has expired")
        user.password_hash = hash_password(body.new_password)
        user.password_reset_token = None
        user.password_reset_expires = None
        return SuccessResponse(message="Password reset successfully")

    # Check admin users
    result = await db.execute(
        select(AdminUser).where(AdminUser.password_reset_token == body.token)
    )
    admin = result.scalar_one_or_none()
    if admin:
        if admin.password_reset_expires and admin.password_reset_expires < now:
            admin.password_reset_token = None
            admin.password_reset_expires = None
            raise AuthenticationError("Reset token has expired")
        admin.password_hash = hash_password(body.new_password)
        admin.password_reset_token = None
        admin.password_reset_expires = None
        return SuccessResponse(message="Password reset successfully")

    raise AuthenticationError("Invalid or expired reset token")


@router.post("/verify-email", response_model=SuccessResponse)
async def verify_email(
    body: VerifyEmailRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SuccessResponse:
    """Verify email address with token from welcome email."""
    result = await db.execute(
        select(OrgUser).where(OrgUser.email_verification_token == body.token)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise AuthenticationError("Invalid verification token")

    user.status = OrgUserStatus.ACTIVE
    user.email_verification_token = None

    return SuccessResponse(message="Email verified successfully")


@router.post("/2fa/setup", response_model=Setup2FAResponse)
async def setup_2fa(
    current_user: Annotated[AdminUser | OrgUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Setup2FAResponse:
    """Generate TOTP secret for 2FA setup (admin or org user)."""
    secret = generate_totp_secret()
    uri = get_totp_uri(secret, current_user.email)

    # Store secret (not yet enabled until verified)
    if isinstance(current_user, AdminUser):
        result = await db.execute(
            select(AdminUser).where(AdminUser.id == current_user.id)
        )
    else:
        result = await db.execute(
            select(OrgUser).where(OrgUser.id == current_user.id)
        )
    user = result.scalar_one()
    user.totp_secret = secret

    # Issue a temp token so the user can verify immediately
    user_type = "admin" if isinstance(current_user, AdminUser) else "org_user"
    temp_token = create_access_token(
        current_user.id,
        extra_claims={"user_type": user_type, "2fa_pending": True, "2fa_setup": True},
    )

    return Setup2FAResponse(secret=secret, uri=uri, temp_token=temp_token)


@router.post("/2fa/verify", response_model=Verify2FAResponse)
async def verify_2fa(
    body: Verify2FARequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Verify2FAResponse:
    """Verify TOTP code during 2FA login flow."""
    try:
        payload = decode_token(body.temp_token)
    except Exception:
        # Try lenient decode to determine if this was a setup or login flow
        msg = "Session expired. Please login again."
        try:
            expired = jwt.decode(
                body.temp_token,
                settings.jwt_secret_key,
                algorithms=[settings.jwt_algorithm],
                options={"verify_exp": False},
            )
            if expired.get("2fa_setup"):
                msg = "2FA setup timeout. Please start the setup again."
        except Exception:
            pass
        raise AuthenticationError(msg)

    if not payload.get("2fa_pending"):
        if payload.get("2fa_setup"):
            raise AuthenticationError("2FA setup timeout. Please start the setup again.")
        raise AuthenticationError("Session expired. Please login again.")

    user_id = payload.get("sub")
    user_type = payload.get("user_type")

    if user_type == "admin":
        result = await db.execute(select(AdminUser).where(AdminUser.id == user_id))
        user = result.scalar_one_or_none()
    else:
        result = await db.execute(select(OrgUser).where(OrgUser.id == user_id))
        user = result.scalar_one_or_none()

    if not user or not user.totp_secret:
        raise AuthenticationError("2FA not configured")

    if not verify_totp(user.totp_secret, body.code):
        raise AuthenticationError("Invalid 2FA code")

    # Enable 2FA if first time
    if not user.totp_enabled:
        user.totp_enabled = True

    # Issue full tokens
    extra_claims = {"user_type": user_type}
    if user_type == "admin":
        extra_claims["role"] = user.role.value
    elif user_type == "org_user":
        extra_claims["org_id"] = str(user.organization_id)
        extra_claims["role"] = user.role_label.value

    access_token = create_access_token(user.id, extra_claims=extra_claims)
    refresh_token = create_refresh_token(user.id)

    return Verify2FAResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post("/2fa/remove", response_model=SuccessResponse)
async def remove_2fa(
    body: Remove2FARequest,
    current_user: Annotated[AdminUser | OrgUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SuccessResponse:
    """Disable 2FA. Requires password and a valid TOTP code."""
    # Re-fetch from DB for mutation
    if isinstance(current_user, AdminUser):
        result = await db.execute(
            select(AdminUser).where(AdminUser.id == current_user.id)
        )
    else:
        result = await db.execute(
            select(OrgUser).where(OrgUser.id == current_user.id)
        )
    user = result.scalar_one()

    if not user.totp_enabled and not user.totp_secret:
        raise AuthenticationError("2FA is not enabled on this account")

    if not verify_password(body.password, user.password_hash):
        raise AuthenticationError("Incorrect password")

    if user.totp_secret and not verify_totp(user.totp_secret, body.code):
        raise AuthenticationError("Invalid 2FA code")

    user.totp_secret = None
    user.totp_enabled = False

    return SuccessResponse(message="2FA has been removed successfully")


@router.post("/change-password", response_model=SuccessResponse)
async def change_password(
    body: ChangePasswordRequest,
    current_user: Annotated[OrgUser, Depends(get_current_org_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SuccessResponse:
    """Change own password (authenticated users)."""
    result = await db.execute(select(OrgUser).where(OrgUser.id == current_user.id))
    user = result.scalar_one()

    if not verify_password(body.current_password, user.password_hash):
        raise AuthenticationError("Current password is incorrect")

    user.password_hash = hash_password(body.new_password)

    return SuccessResponse(message="Password changed successfully")
