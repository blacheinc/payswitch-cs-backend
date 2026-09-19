"""Security utilities – JWT, password hashing, API key management, TOTP."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import string
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pyotp
from jose import jwt
from passlib.context import CryptContext

from app.config import get_settings

settings = get_settings()

# ── Password Hashing ─────────────────────────────────────────────────────────

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    return pwd_context.verify(plain_password, hashed_password)


# ── JWT Tokens ────────────────────────────────────────────────────────────────


def create_access_token(
    subject: str | UUID,
    extra_claims: dict[str, Any] | None = None,
    expires_delta: timedelta | None = None,
) -> str:
    """Create a short-lived access JWT."""
    issued_at = datetime.now(UTC)
    expire = issued_at + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))
    to_encode: dict[str, Any] = {
        "sub": str(subject),
        "iat": issued_at,
        "exp": expire,
        "type": "access",
    }
    if extra_claims:
        to_encode.update(extra_claims)
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(
    subject: str | UUID,
    expires_delta: timedelta | None = None,
) -> str:
    """Create a long-lived refresh JWT."""
    issued_at = datetime.now(UTC)
    expire = issued_at + (expires_delta or timedelta(days=settings.refresh_token_expire_days))
    to_encode: dict[str, Any] = {
        "sub": str(subject),
        "iat": issued_at,
        "exp": expire,
        "type": "refresh",
        "jti": secrets.token_hex(16),
    }
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT. Raises JWTError on failure."""
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])


# ── API Key Generation ────────────────────────────────────────────────────────


def generate_api_key(environment: str = "production") -> tuple[str, str, str]:
    """Generate an API key.

    Returns:
        (full_key, key_prefix, key_hash) where:
        - full_key: the complete key to give to the user (shown only once)
        - key_prefix: first 12 chars for identification
        - key_hash: SHA-256 hash for storage
    """
    prefix = (
        settings.api_key_prefix_live
        if environment == "production"
        else settings.api_key_prefix_test
    )
    random_part = secrets.token_hex(24)  # 48 hex chars
    full_key = f"{prefix}{random_part}"
    key_prefix = full_key[:12]
    key_hash = hash_api_key(full_key)
    return full_key, key_prefix, key_hash


def hash_api_key(key: str) -> str:
    """SHA-256 hash of an API key for secure storage."""
    return hashlib.sha256(key.encode()).hexdigest()


# ── TOTP (Two-Factor Authentication) ─────────────────────────────────────────


def generate_totp_secret() -> str:
    """Generate a new TOTP secret for 2FA setup."""
    return pyotp.random_base32()


def get_totp_uri(secret: str, email: str) -> str:
    """Get the provisioning URI for QR code generation."""
    return pyotp.totp.TOTP(secret).provisioning_uri(
        name=email,
        issuer_name=settings.app_name,
    )


def verify_totp(secret: str, code: str) -> bool:
    """Verify a TOTP code against the secret."""
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)  # Allow 30s clock skew


# ── Webhook Signing ──────────────────────────────────────────────────────────


def generate_webhook_secret() -> str:
    """Generate a secret for webhook HMAC signing."""
    return secrets.token_hex(32)


def sign_webhook_payload(payload: str | bytes, secret: str) -> str:
    """Sign a webhook payload with HMAC-SHA256."""
    if isinstance(payload, str):
        payload = payload.encode()
    signature = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={signature}"


def verify_webhook_signature(payload: str | bytes, signature: str, secret: str) -> bool:
    """Verify a webhook HMAC-SHA256 signature."""
    expected = sign_webhook_payload(payload, secret)
    return hmac.compare_digest(expected, signature)


# ── Misc ─────────────────────────────────────────────────────────────────────


def generate_temp_password() -> str:
    """Generate a temporary password for new accounts.

    Must satisfy ``validate_password_strength`` so a system-generated credential
    can never be weaker than one a user is allowed to choose.
    """
    alphabet = string.ascii_letters + string.digits
    while True:
        body = "".join(secrets.choice(alphabet) for _ in range(20))
        candidate = body + secrets.choice("!@#$%^&*")
        try:
            validate_password_strength(candidate)
        except Exception:  # noqa: BLE001 — regenerate until it satisfies the policy
            continue
        return candidate


def generate_email_verification_token() -> str:
    """Generate a token for email verification / password reset."""
    return secrets.token_urlsafe(32)


# ── Password policy (VAPT finding 7) ─────────────────────────────────────────

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128

# Passwords seen repeatedly in credential-stuffing lists. Not a substitute for a
# real breached-password check (HaveIBeenPwned's range API is the eventual fix),
# but it cheaply rejects what automated attacks actually try first.
_COMMON_PASSWORDS = frozenset(
    {
        "password", "password1", "password123", "passw0rd", "p@ssw0rd",
        "12345678", "123456789", "1234567890", "qwerty123", "qwertyuiop",
        "letmein", "welcome1", "admin123", "administrator", "iloveyou",
        "monkey123", "football", "baseball", "sunshine", "princess",
        "dragon123", "abc12345", "changeme", "trustno1", "starwars",
    }
)


def validate_password_strength(password: str) -> None:
    """Raise ``ValidationError`` unless ``password`` meets the server-side policy.

    Client-side strength meters are a UX affordance only: the API is reachable
    directly, so every path that sets a password must call this. Collects all
    failures so the caller fixes them in one round trip rather than discovering
    them one at a time.
    """
    from app.core.exceptions import ValidationError

    problems: list[str] = []

    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        problems.append(f"be at most {MAX_PASSWORD_LENGTH} characters")
    if not any(c.islower() for c in password):
        problems.append("contain a lowercase letter")
    if not any(c.isupper() for c in password):
        problems.append("contain an uppercase letter")
    if not any(c.isdigit() for c in password):
        problems.append("contain a digit")
    if not any(not c.isalnum() for c in password):
        problems.append("contain a symbol")

    stripped = password.strip().lower()
    if stripped in _COMMON_PASSWORDS:
        problems.append("not be a commonly used password")
    # "12345678", "aaaaaaaa" and friends pass the class checks only by accident;
    # reject anything with no character variety at all.
    elif len(set(stripped)) < 5:
        problems.append("use a wider variety of characters")

    if problems:
        raise ValidationError(
            "Password must " + "; ".join(problems),
            details={"requirements": problems},
        )
