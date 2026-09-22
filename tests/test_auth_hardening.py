"""Regression tests for the VAPT auth findings.

Covers every backend finding in the report: the 2FA enrollment lockout (4), the
missing rate limiting on login / forgot-password (1 and 5), the unvalidated
``callback_url`` in emailed links (2), 2FA re-enrollment without re-auth (3),
unforced password change on emailed credentials (6), the absent server-side
password policy (7), and unmasked PII in responses (9).
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


# ── Finding 2: callback_url must be allow-listed before it reaches an email ──


def _email_service_with_hosts():
    from app.config import Settings
    from app.services.email import EmailService

    svc = EmailService()
    svc.settings = Settings(
        allowed_origins=["https://payswitch-cs.vercel.app", "http://localhost:3000"],
        dashboard_base_url="https://payswitch-cs.vercel.app",
    )
    return svc


def test_trusted_hosts_are_derived_from_existing_config() -> None:
    """A correctly configured deployment needs no extra setting."""
    svc = _email_service_with_hosts()
    assert svc.settings.trusted_callback_hosts() == {"payswitch-cs.vercel.app", "localhost"}


@pytest.mark.parametrize(
    "url",
    [
        "https://simar.space/",  # the attacker domain from the report's PoC
        "https://payswitch-cs.vercel.app.evil.com/x",  # suffix-append
        "https://evil-payswitch-cs.vercel.app/x",  # prefix-append
        "https://payswitch-cs.vercel.app@evil.com/x",  # userinfo resolves to evil.com
        "http://payswitch-cs.vercel.app/x",  # downgrade to plaintext
        "javascript:alert(1)",
        "//evil.com/x",  # scheme-relative
        "",
        None,
    ],
)
def test_untrusted_callback_urls_are_rejected(url: str | None) -> None:
    assert _email_service_with_hosts()._safe_callback(url, "test") is None


@pytest.mark.parametrize(
    "url",
    [
        "https://payswitch-cs.vercel.app/reset-password",
        "http://localhost:3000/reset-password",  # http allowed only for loopback
    ],
)
def test_trusted_callback_urls_are_accepted(url: str) -> None:
    assert _email_service_with_hosts()._safe_callback(url, "test") == url


def test_every_callback_consuming_email_is_validated() -> None:
    """All three builders must go through the check, not just password reset.

    The report only flagged forgot-password, but invite and provision embed the
    same client-supplied value into links sent from our own sender.
    """
    from pathlib import Path

    source = Path("app/services/email.py").read_text(encoding="utf-8")

    for builder in ("send_password_reset_email", "send_invite_email", "send_provision_email"):
        block = source.split(f"def {builder}(")[1].split("\n    def ")[0]
        assert "_safe_callback" in block, f"{builder} does not validate callback_url"
        # The raw value must never reach the link directly.
        assert "link = callback_url" not in block
        assert "{callback_url}" not in block


# ── Finding 3: replacing an active 2FA secret needs the current factor ───────


def test_2fa_setup_requires_reauth_when_already_enabled() -> None:
    source = _login_gate_source()
    setup = source.split('@router.post("/2fa/setup"')[1].split("@router.post")[0]

    assert "current_user.totp_enabled" in setup, "setup does not branch on existing 2FA"
    assert "verify_totp(" in setup, "current TOTP code is not accepted as re-auth"
    assert "verify_password(" in setup, "current password is not accepted as re-auth"
    assert "AuthenticationError" in setup, "re-enrollment is not refused without re-auth"
    # The secret must only be minted after the re-auth branch.
    assert setup.index("current_user.totp_enabled") < setup.index("generate_totp_secret()")


# ── Finding 6: emailed credentials must not survive first login ──────────────


def test_temp_password_issuers_flag_the_account() -> None:
    """Every route that emails a generated password must set the flag."""
    from pathlib import Path

    for name in ("admin_admins", "admin_organizations", "org_portal"):
        src = Path(f"app/routers/{name}.py").read_text(encoding="utf-8")
        if "generate_temp_password()" not in src:
            continue
        assert src.count("must_change_password") >= src.count("generate_temp_password()"), (
            f"{name}: a temp password is issued without flagging the account"
        )


def test_login_withholds_a_usable_session_until_the_password_changes() -> None:
    source = _login_gate_source()
    login = source.split('@router.post("/login"')[1].split("@router.post")[0]

    assert login.count("must_change_password") == 2, "gate missing on one of the two branches"
    assert login.count("pwd_change_required") == 2
    # No refresh token, so the scoped session cannot be extended.
    assert login.count('refresh_token=""') >= 2


def test_scoped_token_is_rejected_everywhere_but_change_password() -> None:
    from pathlib import Path

    deps = Path("app/dependencies.py").read_text(encoding="utf-8")
    guard = deps.split("async def get_current_user(")[1].split("\nasync def ")[0]
    assert 'payload.get("pwd_change_required")' in guard, (
        "the scoped token is not rejected by the shared auth dependency"
    )
    # ...but the one route that clears the flag must still accept it.
    assert "async def get_user_pending_password_change(" in deps

    source = _login_gate_source()
    change = source.split('@router.post("/change-password"')[1].split("@router.post")[0]
    assert "get_user_pending_password_change" in change
    assert "must_change_password = False" in change, "the flag is never cleared"


def test_reset_password_also_clears_the_flag() -> None:
    """Choosing a password via the reset flow satisfies the forced change too.

    Otherwise a flagged user who resets their password is still handed a scoped
    token on the next login, for a credential they chose themselves.
    """
    source = _login_gate_source()
    reset = source.split('@router.post("/reset-password"')[1].split("@router.post")[0]
    assert reset.count("must_change_password = False") == 2, "org and admin branches both needed"


def test_change_password_covers_admins_too() -> None:
    """An admin issued a temp password must be able to clear its own flag."""
    source = _login_gate_source()
    change = source.split('@router.post("/change-password"')[1].split("@router.post")[0]
    assert "AdminUser" in change, "change-password is org-only; flagged admins would be stranded"


# ── Finding 7: the server enforces the password policy ───────────────────────


@pytest.mark.parametrize(
    "password",
    [
        "12345678",  # the weak value accepted in the report's PoC
        "password",
        "Passw0rd!",  # good classes, too short
        "aaaaaaaaaaaaA1!",  # long enough, no character variety
        "short1!A",
        "",
    ],
)
def test_weak_passwords_are_rejected(password: str) -> None:
    from app.core.exceptions import ValidationError
    from app.core.security import validate_password_strength

    with pytest.raises(ValidationError):
        validate_password_strength(password)


def test_strong_password_is_accepted() -> None:
    from app.core.security import validate_password_strength

    validate_password_strength("Str0ng&Passphrase99")


def test_generated_temp_passwords_satisfy_the_policy() -> None:
    """A system-issued credential must never be weaker than a user-chosen one."""
    from app.core.security import generate_temp_password, validate_password_strength

    for _ in range(25):
        validate_password_strength(generate_temp_password())


def test_password_setting_routes_validate() -> None:
    source = _login_gate_source()
    for route in ("/reset-password", "/change-password"):
        block = source.split(f'@router.post("{route}"')[1].split("@router.post")[0]
        assert "validate_password_strength(" in block, f"{route} does not enforce the policy"


# ── Finding 9: identifiers are masked on the way out ─────────────────────────


def test_masking_keeps_only_a_short_tail() -> None:
    from app.core.masking import mask_national_id, mask_phone

    assert mask_national_id("GHA-2342344234-3") == "\u2022" * 12 + "34-3"
    assert mask_phone("+233904887364") == "\u2022" * 10 + "364"


def test_short_values_are_masked_entirely() -> None:
    """A tail longer than the value would defeat the point."""
    from app.core.masking import mask_national_id

    assert set(mask_national_id("1234") or "") == {"\u2022"}
    assert mask_national_id(None) is None


def test_applicant_summary_masks_on_serialization() -> None:
    from app.schemas.score_request import ApplicantSummary

    dumped = ApplicantSummary(
        full_name="Ama Mensah",
        national_id_number="GHA-2342344234-3",
        phone="+233904887364",
        account_number="0123456789",
    ).model_dump()

    assert dumped["full_name"] == "Ama Mensah", "name is not an identifier; leave it alone"
    for field in ("national_id_number", "phone", "account_number"):
        assert "\u2022" in dumped[field], f"{field} left unmasked"
    assert "2342344234" not in dumped["national_id_number"]


def test_inbound_schema_is_not_masked() -> None:
    """Masking must not touch what is stored or scored."""
    from app.schemas.score_request import ApplicantInfo

    assert "field_serializer" not in ApplicantInfo.__doc__ if ApplicantInfo.__doc__ else True
    from datetime import date

    info = ApplicantInfo(
        full_name="Ama Mensah", date_of_birth=date(2000, 1, 1), national_id_number="GHA-123456789"
    )
    assert info.model_dump()["national_id_number"] == "GHA-123456789"


# ── Finding 9, follow-ups: bureau lookup + the audited reveal route ──────────


def test_bureau_personal_details_masks_identifiers() -> None:
    """/bureau-lookup takes `identification` as OPTIONAL.

    A caller can search by name + date of birth and read the consumer's national
    ID back, which is identifier harvesting rather than over-display. Not in the
    VAPT report; found afterwards.
    """
    from app.schemas.bureau_schemas import BureauPersonalDetails

    dumped = BureauPersonalDetails(
        consumer_id="GHA-2342344234-3",
        national_id="GHA-2342344234-3",
        phone="+233904887364",
    ).model_dump()

    assert "\u2022" in dumped["national_id"]
    assert "\u2022" in dumped["phone"]
    assert "2342344234" not in dumped["national_id"]


def test_bureau_consumer_id_is_not_masked() -> None:
    """consumer_id is echoed back as BureauData.consumer_id when scoring.

    Masking it would feed a masked value into the scoring pipeline.
    """
    from app.schemas.bureau_schemas import BureauPersonalDetails

    dumped = BureauPersonalDetails(consumer_id="GHA-2342344234-3").model_dump()
    assert dumped["consumer_id"] == "GHA-2342344234-3"


def test_reveal_schema_does_not_mask() -> None:
    """The reveal route is the one place full values are meant to surface."""
    from app.schemas.score_request import ApplicantPiiReveal

    dumped = ApplicantPiiReveal(
        request_id="SCR-1", national_id_number="GHA-2342344234-3", phone="+233904887364"
    ).model_dump()
    assert dumped["national_id_number"] == "GHA-2342344234-3"
    assert dumped["phone"] == "+233904887364"


def test_reveal_route_always_audits() -> None:
    """The audit write must be unconditional, not admin-only like the detail route."""
    from pathlib import Path

    src = Path("app/routers/score_requests.py").read_text(encoding="utf-8")
    block = src.split('@router.get("/{request_id}/applicant-pii"')[1].split("@router.")[0]

    assert "AuditLog(" in block
    assert "score_requests.reveal_applicant_pii" in block
    # An `if auth.user_type == "admin"` guard around the audit would mean org
    # users could read full identifiers leaving no trace.
    assert 'if auth.user_type == "admin"' not in block
    # Non-admins must still be scoped to their own organisation.
    assert 'if auth.user_type != "admin"' in block
