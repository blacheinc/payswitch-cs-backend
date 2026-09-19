"""Authentication schemas."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    requires_2fa: bool = False
    # True when the account still holds a system-generated password. The token
    # issued alongside it only permits /auth/change-password.
    requires_password_change: bool = False
    user_type: str  # "admin" or "org_user"
    user: dict | None = None


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class ForgotPasswordRequest(BaseModel):
    email: EmailStr
    callback_url: str | None = None


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


class VerifyEmailRequest(BaseModel):
    token: str


class Setup2FARequest(BaseModel):
    """Re-authentication for 2FA (re-)enrollment.

    Only required when the account already has 2FA active — proving control of
    the current factor before it can be replaced (VAPT finding 3).
    """

    password: str | None = None
    code: str | None = None


class Setup2FAResponse(BaseModel):
    secret: str
    uri: str
    temp_token: str
    message: str = "Scan the QR code with your authenticator app, then verify with a code"


class Verify2FARequest(BaseModel):
    code: str = Field(min_length=6, max_length=6)
    temp_token: str


class Verify2FAResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    message: str = "2FA verification successful"


class Remove2FARequest(BaseModel):
    password: str = Field(min_length=8, max_length=128)
    code: str = Field(min_length=6, max_length=6)


class UserProfileResponse(BaseModel):
    id: str
    email: str
    name: str
    user_type: str  # "admin" or "org_user"
    role: str
    status: str
    totp_enabled: bool
    organization_id: str | None = None
    last_login_at: str | None = None
    created_at: str | None = None
    # Resolved RBAC permission codes for this user.
    # ["*"] means super-admin — skip individual checks.
    # Empty list means no role has been assigned yet.
    permissions: list[str] = []


class UserPermissionsResponse(BaseModel):
    """Returned by GET /auth/me/permissions."""

    user_id: str
    user_type: str  # "admin", "org_user", or "api_key"
    # Resolved permission codes.  ["*"] = SUPER_ADMIN wildcard.
    permissions: list[str]
    is_super_admin: bool
