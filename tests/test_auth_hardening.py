"""Regression tests for the VAPT auth findings.

Covers the 2FA enrollment lockout (report finding 4) and the missing rate
limiting on login / forgot-password (findings 1 and 5).
"""

from __future__ import annotations

import asyncio

import pytest

# ── Finding 4: abandoned 2FA enrollment must not lock the account ────────────


def _login_gate_source() -> str:
    from pathlib import Path

    return Path("app/routers/auth.py").read_text(encoding="utf-8")


def test_login_does_not_gate_2fa_on_an_unconfirmed_secret() -> None:
    """/2fa/setup writes ``totp_secret`` before the user confirms a code.

    Gating login on it locks out anyone who abandons enrollment: they are
    prompted for a TOTP code no authenticator can produce. The gate must key
    off ``totp_enabled``, which is only set after a valid code in /2fa/verify.
    """
    source = _login_gate_source()

    assert "totp_enabled or admin.totp_secret" not in source
    assert "totp_enabled or org_user.totp_secret" not in source
    assert "if admin.totp_enabled:" in source
    assert "if org_user.totp_enabled:" in source


def test_2fa_enabled_flag_is_only_set_after_code_verification() -> None:
    """The enable flag belongs in /2fa/verify, never in /2fa/setup."""
    source = _login_gate_source()

    setup = source.split('@router.post("/2fa/setup"')[1].split("@router.post")[0]
    verify = source.split('@router.post("/2fa/verify"')[1].split("@router.post")[0]

    assert "totp_enabled = True" not in setup
    assert "user.totp_secret = secret" in setup
    assert "user.totp_enabled = True" in verify


# ── Findings 1 & 5: rate limiting on unauthenticated auth endpoints ──────────


def test_auth_limiter_blocks_once_the_limit_is_exceeded() -> None:
    from app.core.rate_limiter import AuthRateLimiter

    async def run() -> list[bool]:
        limiter = AuthRateLimiter(None)  # no Redis -> in-process fallback
        return [await limiter.allow("unit:email:a@b.com", 5, 900) for _ in range(8)]

    assert asyncio.run(run()) == [True] * 5 + [False] * 3


def test_auth_limiter_keys_are_independent() -> None:
    from app.core.rate_limiter import AuthRateLimiter

    async def run() -> bool:
        limiter = AuthRateLimiter(None)
        for _ in range(5):
            await limiter.allow("unit:indep:one", 5, 900)
        # A different key must not inherit the exhausted bucket.
        return await limiter.allow("unit:indep:two", 5, 900)

    assert asyncio.run(run()) is True


def test_auth_limiter_degrades_instead_of_failing_open() -> None:
    """A Redis error must fall back to the local window, not allow everything.

    The org limiter in ``check_rate_limit`` fails open by design; on login that
    would reopen the brute-force window this limiter exists to close.
    """
    from app.core.rate_limiter import AuthRateLimiter

    class BrokenRedis:
        def pipeline(self, *_args, **_kwargs):
            raise ConnectionError("redis down")

    async def run() -> list[bool]:
        limiter = AuthRateLimiter(BrokenRedis())
        return [await limiter.allow("unit:broken", 3, 900) for _ in range(5)]

    assert asyncio.run(run()) == [True] * 3 + [False] * 2


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"x-forwarded-for": "203.0.113.9, 70.41.3.18"}, "203.0.113.9"),
        ({"x-forwarded-for": "  198.51.100.4  "}, "198.51.100.4"),
        ({}, "127.0.0.1"),
    ],
)
def test_client_ip_prefers_forwarded_header(headers: dict[str, str], expected: str) -> None:
    from app.core.rate_limiter import client_ip

    class FakeClient:
        host = "127.0.0.1"

    class FakeRequest:
        def __init__(self, hdrs: dict[str, str]) -> None:
            self.headers = hdrs
            self.client = FakeClient()

    assert client_ip(FakeRequest(headers)) == expected  # type: ignore[arg-type]


def test_login_and_forgot_password_enforce_limits() -> None:
    """Both endpoints must consult the limiter on IP *and* target email."""
    source = _login_gate_source()

    login = source.split('@router.post("/login"')[1].split("@router.post")[0]
    forgot = source.split('@router.post("/forgot-password"')[1].split("@router.post")[0]

    for block in (login, forgot):
        assert "limiter.allow" in block
        assert "client_ip(request)" in block

    # Login rejects loudly; password reset must stay enumeration-safe.
    assert "RateLimitError" in login
    assert "_RESET_SENT_MESSAGE" in forgot
    assert "RateLimitError" not in forgot


def test_forgot_password_response_is_identical_when_throttled() -> None:
    """The throttled and unthrottled replies must be byte-identical."""
    source = _login_gate_source()
    forgot = source.split('@router.post("/forgot-password"')[1].split("@router.post")[0]

    # Exactly two returns, both the shared constant — no literal that could drift.
    assert forgot.count("return SuccessResponse(message=_RESET_SENT_MESSAGE)") == 2
    assert 'SuccessResponse(message="If that email' not in forgot
