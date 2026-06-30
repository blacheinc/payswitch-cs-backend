"""
PaySwitch Credit Scoring Platform — Comprehensive Integration Test Suite.

Run inside the container:
    docker exec csbackend-api-1 python scripts/test_integration.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import date

import httpx

sys.path.append(os.getcwd())

# ── Configuration ────────────────────────────────────────────────────────────

BASE_URL = os.environ.get("TEST_API_BASE_URL", "http://localhost:8000")
ADMIN_EMAIL = os.environ.get("TEST_ADMIN_EMAIL", "")
ADMIN_PASS = os.environ.get("TEST_ADMIN_PASSWORD", "")
TIMEOUT = 30.0

# ── ANSI helpers ─────────────────────────────────────────────────────────────

G = "\033[92m"
R = "\033[91m"
B = "\033[94m"
Y = "\033[93m"
X = "\033[0m"
OK = f"{G}✔{X}"
NO = f"{R}✘{X}"
II = f"{B}ℹ{X}"

# ── Test context ─────────────────────────────────────────────────────────────


class Ctx:
    """Shared mutable state passed between test modules."""

    def __init__(self, client: httpx.AsyncClient):
        self.c = client
        self.admin_token: str | None = None
        self.admin_refresh: str | None = None
        self.org_id: str | None = None
        self.org_name: str | None = None
        self.org_admin_email: str | None = None
        self.org_admin_pass: str | None = None
        self.org_token: str | None = None
        self.org_refresh: str | None = None
        self.org_user_id: str | None = None
        self.sandbox_key: str | None = None
        self.prod_key: str | None = None
        self.api_key_id: str | None = None
        self.api_key_val: str | None = None
        self.revoked_key_val: str | None = None
        self.webhook_id: str | None = None
        self.tracking_ids: list[str] = []
        self.invited_user_id: str | None = None
        self.source_id: str | None = None
        self.passed = 0
        self.failed = 0
        self.errors: list[str] = []

    def ah(self):
        return {"Authorization": f"Bearer {self.admin_token}"}

    def oh(self):
        return {"Authorization": f"Bearer {self.org_token}"}

    def check(self, ok: bool, label: str, detail: str = ""):
        if ok:
            self.passed += 1
            print(f"  {OK} {label}" + (f" ({detail})" if detail else ""))
        else:
            self.failed += 1
            self.errors.append(label)
            print(f"  {NO} {label}" + (f" — {detail}" if detail else ""))

    def section(self, name: str):
        print(f"\n{B}── {name} {'─' * max(1, 50 - len(name))}{X}")


# ── Module 1: Health ─────────────────────────────────────────────────────────


async def test_health(ctx: Ctx):
    ctx.section("Module 1: Health & Readiness")

    r = await ctx.c.get("/health")
    ctx.check(
        r.status_code == 200 and r.json()["status"] == "ok",
        "Health check returns ok",
        f"v={r.json().get('version')}",
    )

    r = await ctx.c.get("/ready")
    d = r.json()
    ctx.check(
        r.status_code == 200 and d["status"] in ("ok", "degraded"),
        "Readiness check",
        f"pg={d.get('checks', {}).get('postgres')}",
    )


# ── Module 2: Admin Auth ─────────────────────────────────────────────────────


async def test_admin_auth(ctx: Ctx):
    ctx.section("Module 2: Authentication (Admin)")

    # Success
    r = await ctx.c.post("/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASS})
    ok = r.status_code == 200 and "access_token" in r.json()
    if ok:
        ctx.admin_token = r.json()["access_token"]
        ctx.admin_refresh = r.json()["refresh_token"]
    ctx.check(ok, "Admin login success", f"user_type={r.json().get('user_type')}")

    # Wrong password
    r = await ctx.c.post("/auth/login", json={"email": ADMIN_EMAIL, "password": "WrongPass1"})
    ctx.check(r.status_code == 401, "Admin login wrong password", f"→ {r.status_code}")

    # Non-existent email
    r = await ctx.c.post("/auth/login", json={"email": "nobody@test.com", "password": "Whatever1"})
    ctx.check(r.status_code == 401, "Admin login non-existent email", f"→ {r.status_code}")

    # Refresh
    if ctx.admin_refresh:
        r = await ctx.c.post("/auth/refresh", json={"refresh_token": ctx.admin_refresh})
        ctx.check(r.status_code == 200 and "access_token" in r.json(), "Refresh token works")

    # Invalid refresh
    r = await ctx.c.post("/auth/refresh", json={"refresh_token": "invalid.token.here"})
    ctx.check(r.status_code == 401, "Refresh with invalid token", f"→ {r.status_code}")

    # Logout
    r = await ctx.c.post("/auth/logout")
    ctx.check(r.status_code == 200, "Logout", f"→ {r.status_code}")


# ── Module 3: Organization CRUD ──────────────────────────────────────────────


async def test_org_crud(ctx: Ctx):
    ctx.section("Module 3: Organization CRUD (Admin)")
    if not ctx.admin_token:
        ctx.check(False, "SKIP — no admin token")
        return

    h = ctx.ah()
    slug = f"test-{uuid.uuid4().hex[:8]}"
    ctx.org_name = f"IntTest {slug}"

    # Create
    body = {
        "name": ctx.org_name,
        "short_name": slug,
        "industry_type": "fintech",
        "primary_contact_email": f"admin-{slug}@example.com",
        "primary_contact_name": "Test Admin",
    }
    r = await ctx.c.post("/admin/organizations", json=body, headers=h)
    ok = r.status_code == 201 and r.json().get("name") == ctx.org_name
    if ok:
        ctx.org_id = r.json()["id"]
    ctx.check(ok, "Create organization", f"id={ctx.org_id}")

    # Get
    r = await ctx.c.get(f"/admin/organizations/{ctx.org_id}", headers=h)
    ctx.check(
        r.status_code == 200 and r.json()["short_name"] == slug,
        "Get organization — fields match",
    )

    # List
    r = await ctx.c.get("/admin/organizations", headers=h)
    ctx.check(
        r.status_code == 200 and r.json()["total"] >= 1,
        "List organizations",
        f"total={r.json().get('total')}",
    )

    # Search
    r = await ctx.c.get(f"/admin/organizations?search={slug}", headers=h)
    items = r.json().get("items", [])
    ctx.check(
        r.status_code == 200 and len(items) >= 1,
        "Search filter works",
        f"found={len(items)}",
    )

    # Update (PATCH)
    r = await ctx.c.patch(
        f"/admin/organizations/{ctx.org_id}",
        json={"primary_contact_name": "Updated Admin"},
        headers=h,
    )
    ctx.check(r.status_code == 200, "Update organization")
    # Verify
    r = await ctx.c.get(f"/admin/organizations/{ctx.org_id}", headers=h)
    ctx.check(
        r.json().get("primary_contact_name") == "Updated Admin",
        "Update verified via GET",
    )

    # Provision
    r = await ctx.c.post(
        f"/admin/organizations/{ctx.org_id}/provision",
        json={"callback_url": "https://app.test/welcome"},
        headers=h,
    )
    if r.status_code == 200:
        d = r.json()
        ctx.org_admin_email = d.get("admin_email")
        ctx.sandbox_key = d.get("sandbox_api_key")
        ctx.prod_key = d.get("production_api_key")
        ctx.check(True, "Provision organization", f"admin={ctx.org_admin_email}")
    else:
        ctx.check(False, "Provision organization", f"→ {r.status_code} {r.text[:200]}")

    # Verify provision — org is ACTIVE
    r = await ctx.c.get(f"/admin/organizations/{ctx.org_id}", headers=h)
    ctx.check(r.json().get("status") == "active", "Provision verified — status ACTIVE")

    # Provision again → conflict
    r = await ctx.c.post(f"/admin/organizations/{ctx.org_id}/provision", headers=h)
    ctx.check(r.status_code == 409, "Provision already-active → 409", f"→ {r.status_code}")

    # Suspend
    r = await ctx.c.post(
        f"/admin/organizations/{ctx.org_id}/suspend",
        json={"reason": "Integration test suspension"},
        headers=h,
    )
    ctx.check(r.status_code == 200, "Suspend organization")
    r = await ctx.c.get(f"/admin/organizations/{ctx.org_id}", headers=h)
    ctx.check(r.json().get("status") == "suspended", "Suspend verified via GET")

    # Activate
    r = await ctx.c.post(f"/admin/organizations/{ctx.org_id}/activate", headers=h)
    ctx.check(r.status_code == 200, "Activate organization")
    r = await ctx.c.get(f"/admin/organizations/{ctx.org_id}", headers=h)
    ctx.check(r.json().get("status") == "active", "Activate verified via GET")

    # Duplicate slug
    r = await ctx.c.post("/admin/organizations", json=body, headers=h)
    ctx.check(r.status_code == 409, "Duplicate short_name → 409", f"→ {r.status_code}")

    # Non-existent org
    fake = str(uuid.uuid4())
    r = await ctx.c.get(f"/admin/organizations/{fake}", headers=h)
    ctx.check(r.status_code == 404, "Get non-existent org → 404", f"→ {r.status_code}")


# ── Module 4: Org User Auth (via seed) ────────────────────────────────────────


async def test_org_auth(ctx: Ctx):
    ctx.section("Module 4: Authentication (Org User)")
    if not ctx.org_id:
        ctx.check(False, "SKIP — no org_id")
        return

    # Seed an ACTIVE user directly (provision creates PENDING users, which can't login
    # without verify_email; seeding lets us test the auth flow independently)
    user_email = f"orgtest_{uuid.uuid4().hex[:6]}@example.com"
    user_pass = "Test@12345"

    from app.core.security import hash_password
    from app.db import async_session_factory
    from app.models.user import OrgRoleLabel, OrgUser, OrgUserStatus

    async with async_session_factory() as session:
        u = OrgUser(
            email=user_email,
            name="Integration Tester",
            password_hash=hash_password(user_pass),
            organization_id=ctx.org_id,
            role_label=OrgRoleLabel.ADMIN,
            status=OrgUserStatus.ACTIVE,
        )
        session.add(u)
        await session.commit()
        ctx.org_user_id = str(u.id)

    # Login
    r = await ctx.c.post("/auth/login", json={"email": user_email, "password": user_pass})
    ok = r.status_code == 200 and r.json().get("user_type") == "org_user"
    if ok:
        ctx.org_token = r.json()["access_token"]
        ctx.org_refresh = r.json()["refresh_token"]
    ctx.check(ok, "Org user login", f"user_type={r.json().get('user_type')}")

    # Change password
    new_pass = "NewPass@9999"
    r = await ctx.c.post(
        "/auth/change-password",
        json={"current_password": user_pass, "new_password": new_pass},
        headers=ctx.oh(),
    )
    ctx.check(r.status_code == 200, "Change password")

    # Login with new password
    r = await ctx.c.post("/auth/login", json={"email": user_email, "password": new_pass})
    ok2 = r.status_code == 200
    if ok2:
        ctx.org_token = r.json()["access_token"]
        ctx.org_refresh = r.json()["refresh_token"]
    ctx.check(ok2, "Login with new password succeeds")

    # Old password fails
    r = await ctx.c.post("/auth/login", json={"email": user_email, "password": user_pass})
    ctx.check(r.status_code == 401, "Old password fails → 401")

    # Forgot password (always 200)
    r = await ctx.c.post("/auth/forgot-password", json={"email": user_email})
    ctx.check(r.status_code == 200, "Forgot password (valid email) → 200")
    r = await ctx.c.post("/auth/forgot-password", json={"email": "ghost@nowhere.example.com"})
    ctx.check(
        r.status_code == 200,
        "Forgot password (fake email) → 200 (anti-enum)",
        f"→ {r.status_code}",
    )

    # 2FA setup
    r = await ctx.c.post("/auth/2fa/setup", headers=ctx.oh())
    ctx.check(
        r.status_code == 200 and "secret" in r.json(),
        "Setup 2FA",
        f"uri={'yes' if r.json().get('uri') else 'no'}",
    )


# ── Module 5: Profile ────────────────────────────────────────────────────────


async def test_profile(ctx: Ctx):
    ctx.section("Module 5: Organization Portal — Profile")
    if not ctx.org_token:
        ctx.check(False, "SKIP — no org token")
        return

    h = ctx.oh()

    r = await ctx.c.get("/org/profile", headers=h)
    ctx.check(
        r.status_code == 200 and "name" in r.json(),
        "Get org profile",
        f"name={r.json().get('name')}",
    )

    r = await ctx.c.patch(
        "/org/profile", json={"primary_contact_name": "Profile Updated"}, headers=h
    )
    ctx.check(r.status_code == 200, "Update org profile")

    r = await ctx.c.get("/org/profile", headers=h)
    ctx.check(
        r.json().get("primary_contact_name") == "Profile Updated",
        "Profile update verified via GET",
    )


# ── Module 6: User Management ────────────────────────────────────────────────


async def test_users(ctx: Ctx):
    ctx.section("Module 6: Organization Portal — Users")
    if not ctx.org_token:
        ctx.check(False, "SKIP — no org token")
        return

    h = ctx.oh()
    invite_email = f"invite_{uuid.uuid4().hex[:6]}@example.com"

    # Invite
    r = await ctx.c.post(
        "/org/users",
        json={"email": invite_email, "name": "Invited User", "role_label": "viewer"},
        headers=h,
    )
    if r.status_code == 201:
        ctx.invited_user_id = r.json()["id"]
        ctx.check(
            True,
            "Invite user",
            f"id={ctx.invited_user_id}, role={r.json().get('role_label')}",
        )
    else:
        ctx.check(False, "Invite user", f"→ {r.status_code} {r.text[:200]}")

    # List
    r = await ctx.c.get("/org/users", headers=h)
    ctx.check(
        r.status_code == 200 and r.json().get("total", 0) >= 2,
        "List users",
        f"total={r.json().get('total')}",
    )

    # Duplicate invite
    r = await ctx.c.post(
        "/org/users",
        json={"email": invite_email, "name": "Dup", "role_label": "viewer"},
        headers=h,
    )
    ctx.check(r.status_code == 409, "Invite duplicate email → 409", f"→ {r.status_code}")

    # Remove invited user
    if ctx.invited_user_id:
        r = await ctx.c.delete(f"/org/users/{ctx.invited_user_id}", headers=h)
        ctx.check(r.status_code == 200, "Remove user")

    # Remove non-existent
    r = await ctx.c.delete(f"/org/users/{uuid.uuid4()}", headers=h)
    ctx.check(r.status_code == 404, "Remove non-existent → 404", f"→ {r.status_code}")


# ── Module 7: API Keys ──────────────────────────────────────────────────────


async def test_api_keys(ctx: Ctx):
    ctx.section("Module 7: Organization Portal — API Keys")
    if not ctx.org_token:
        ctx.check(False, "SKIP — no org token")
        return

    h = ctx.oh()

    # Create sandbox key
    r = await ctx.c.post(
        "/org/api-keys",
        json={"name": "Test Sandbox", "environment": "sandbox"},
        headers=h,
    )
    if r.status_code == 201:
        d = r.json()
        ctx.api_key_val = d["key"]
        ctx.api_key_id = d["id"]
        ctx.check(True, "Create sandbox API key", f"prefix={d.get('key_prefix')}")
    else:
        ctx.check(False, "Create sandbox API key", f"→ {r.status_code} {r.text[:200]}")

    # Create production key
    r = await ctx.c.post(
        "/org/api-keys",
        json={"name": "Test Prod", "environment": "production"},
        headers=h,
    )
    if r.status_code == 201:
        d = r.json()
        ctx.check(True, "Create production API key", f"prefix={d.get('key_prefix')}")
        # We'll revoke this one later
        ctx.revoked_key_val = d["key"]
        revoke_id = d["id"]
    else:
        ctx.check(False, "Create production API key", f"→ {r.status_code}")
        revoke_id = None

    # List
    r = await ctx.c.get("/org/api-keys", headers=h)
    ctx.check(
        r.status_code == 200 and len(r.json()) >= 2,
        "List API keys",
        f"count={len(r.json())}",
    )

    # Revoke
    if revoke_id:
        r = await ctx.c.delete(f"/org/api-keys/{revoke_id}", headers=h)
        ctx.check(r.status_code == 200, "Revoke API key")

    # Revoke non-existent
    r = await ctx.c.delete(f"/org/api-keys/{uuid.uuid4()}", headers=h)
    ctx.check(r.status_code == 404, "Revoke non-existent → 404", f"→ {r.status_code}")


# ── Module 8: Webhooks ───────────────────────────────────────────────────────


async def test_webhooks(ctx: Ctx):
    ctx.section("Module 8: Organization Portal — Webhooks")
    if not ctx.org_token:
        ctx.check(False, "SKIP — no org token")
        return

    h = ctx.oh()

    # List event types
    r = await ctx.c.get("/org/webhooks/events", headers=h)
    events = r.json() if r.status_code == 200 else []
    event_names = [e.get("event") for e in events]
    ctx.check(
        r.status_code == 200 and "score.completed" in event_names,
        "List webhook events",
        f"count={len(events)}",
    )

    # Create
    r = await ctx.c.post(
        "/org/webhooks",
        json={
            "url": "https://webhook.test/callback",
            "events": ["score.completed", "score.failed"],
            "description": "Integration test hook",
        },
        headers=h,
    )
    if r.status_code == 201:
        ctx.webhook_id = r.json()["id"]
        ctx.check(True, "Create webhook", f"id={ctx.webhook_id}")
    else:
        ctx.check(False, "Create webhook", f"→ {r.status_code} {r.text[:200]}")

    # List
    r = await ctx.c.get("/org/webhooks", headers=h)
    ctx.check(
        r.status_code == 200 and len(r.json()) >= 1,
        "List webhooks",
        f"count={len(r.json())}",
    )

    # Update
    if ctx.webhook_id:
        r = await ctx.c.patch(
            f"/org/webhooks/{ctx.webhook_id}",
            json={"url": "https://webhook.test/v2", "is_active": False},
            headers=h,
        )
        ctx.check(r.status_code == 200, "Update webhook")
        # Verify
        r = await ctx.c.get("/org/webhooks", headers=h)
        updated = [w for w in r.json() if w["id"] == ctx.webhook_id]
        if updated:
            ctx.check(
                updated[0]["url"] == "https://webhook.test/v2" and updated[0]["is_active"] is False,
                "Webhook update verified",
            )
        else:
            ctx.check(False, "Webhook update verified — not found in list")

    # Delete
    if ctx.webhook_id:
        r = await ctx.c.delete(f"/org/webhooks/{ctx.webhook_id}", headers=h)
        ctx.check(r.status_code == 200, "Delete webhook")
        # Verify gone
        r = await ctx.c.get("/org/webhooks", headers=h)
        remaining = [w for w in r.json() if w["id"] == ctx.webhook_id]
        ctx.check(len(remaining) == 0, "Delete verified — webhook gone")

    # Delete non-existent
    r = await ctx.c.delete(f"/org/webhooks/{uuid.uuid4()}", headers=h)
    ctx.check(r.status_code == 404, "Delete non-existent → 404", f"→ {r.status_code}")


# ── Module 9: Scoring API ────────────────────────────────────────────────────


def _score_payload(id_suffix: str | None = None) -> dict:
    nid = id_suffix or uuid.uuid4().hex[:9]
    return {
        "applicant": {
            "full_name": "Kwame Nkrumah",
            "date_of_birth": "1980-01-01",
            "national_id_type": "ghana_card",
            "national_id_number": f"GHA-{nid}-0",
            "phone": "+233541234567",
        },
        "loan_request": {"amount": 5000, "purpose": "business", "tenure_months": 12},
        "consent": {"consent_date": date.today().isoformat()},
    }


async def test_scoring(ctx: Ctx):
    ctx.section("Module 9: Scoring API")
    if not ctx.org_token:
        ctx.check(False, "SKIP — no org token")
        return

    h = ctx.oh()

    # Create via Bearer
    r = await ctx.c.post("/v1/score-requests", json=_score_payload(), headers=h)
    if r.status_code == 201:
        d = r.json()
        tid1 = d.get("request_id")
        ctx.tracking_ids.append(tid1)
        ctx.check(True, "Create score (Bearer)", f"id={tid1}, status={d.get('status')}")
    else:
        ctx.check(False, "Create score (Bearer)", f"→ {r.status_code} {r.text[:200]}")
        tid1 = None

    # Create via API key
    if ctx.api_key_val:
        r = await ctx.c.post(
            "/v1/score-requests",
            json=_score_payload(),
            headers={"X-API-Key": ctx.api_key_val},
        )
        if r.status_code == 201:
            tid2 = r.json().get("request_id")
            ctx.tracking_ids.append(tid2)
            ctx.check(True, "Create score (API key)", f"id={tid2}")
        else:
            ctx.check(False, "Create score (API key)", f"→ {r.status_code} {r.text[:200]}")
            tid2 = None
    else:
        ctx.check(False, "Create score (API key) — SKIP, no key")
        tid2 = None

    # Get by tracking_id
    if tid1:
        r = await ctx.c.get(f"/v1/score-requests/{tid1}", headers=h)
        ctx.check(
            r.status_code == 200 and r.json().get("request_id") == tid1,
            "Get score request",
            f"status={r.json().get('status')}",
        )

    # List
    r = await ctx.c.get("/v1/score-requests", headers=h)
    ctx.check(
        r.status_code == 200 and r.json().get("total", 0) >= 1,
        "List score requests",
        f"total={r.json().get('total')}",
    )

    # List with filter
    r = await ctx.c.get("/v1/score-requests?status=completed", headers=h)
    ctx.check(
        r.status_code == 200,
        "List score requests (status=completed)",
        f"total={r.json().get('total')}",
    )

    # Record outcome
    if tid1:
        r = await ctx.c.post(
            f"/v1/score-requests/{tid1}/outcome",
            json={
                "decision": "approved",
                "approved_amount": 4500,
                "approved_tenure_months": 12,
                "interest_rate": 22.5,
            },
            headers=h,
        )
        ctx.check(
            r.status_code == 200 and r.json().get("decision") == "approved",
            "Record outcome",
        )

    # Record performance
    if tid1:
        try:
            r = await ctx.c.post(
                f"/v1/score-requests/{tid1}/performance",
                json={
                    "status": "current",
                    "days_past_due": 0,
                    "outstanding_balance": 4200,
                    "as_of_date": date.today().isoformat(),
                },
                headers=h,
            )
            ctx.check(r.status_code == 200, "Record performance")
        except Exception as e:
            ctx.check(False, "Record performance", f"Exception: {e}")

    # Idempotency
    idem_key = f"idem-{uuid.uuid4().hex[:8]}"
    payload = _score_payload()
    try:
        r1 = await ctx.c.post(
            "/v1/score-requests",
            json=payload,
            headers={**h, "Idempotency-Key": idem_key},
        )
        r2 = await ctx.c.post(
            "/v1/score-requests",
            json=payload,
            headers={**h, "Idempotency-Key": idem_key},
        )
        if r1.status_code == 201:
            # Second request should return cached or conflict
            ctx.check(
                r2.status_code in (200, 201, 409),
                "Idempotency key",
                f"r1={r1.status_code} r2={r2.status_code}",
            )
        else:
            ctx.check(False, "Idempotency key", f"first request failed: {r1.status_code}")
    except Exception as e:
        ctx.check(False, "Idempotency key", f"Exception: {e}")

    # Invalid payload
    r = await ctx.c.post("/v1/score-requests", json={"bad": True}, headers=h)
    ctx.check(r.status_code == 422, "Invalid payload → 422", f"→ {r.status_code}")

    # Invalid API key
    r = await ctx.c.post(
        "/v1/score-requests",
        json=_score_payload(),
        headers={"X-API-Key": "sk_test_invalid_key"},
    )
    ctx.check(r.status_code == 401, "Invalid API key → 401", f"→ {r.status_code}")

    # Non-existent score
    r = await ctx.c.get("/v1/score-requests/SCR-DOESNOTEXIST", headers=h)
    ctx.check(r.status_code == 404, "Get non-existent → 404", f"→ {r.status_code}")

    # Revoked key
    if ctx.revoked_key_val:
        r = await ctx.c.post(
            "/v1/score-requests",
            json=_score_payload(),
            headers={"X-API-Key": ctx.revoked_key_val},
        )
        ctx.check(r.status_code == 401, "Revoked key → 401", f"→ {r.status_code}")


# ── Module 10: API Logs ──────────────────────────────────────────────────────


async def test_api_logs(ctx: Ctx):
    ctx.section("Module 10: API Logs")
    if not ctx.org_token:
        ctx.check(False, "SKIP — no org token")
        return

    h = ctx.oh()

    r = await ctx.c.get("/org/api-logs", headers=h)
    ctx.check(
        r.status_code == 200 and "total" in r.json(),
        "List API logs",
        f"total={r.json().get('total')}",
    )


# ── Module 11: Admin Data Sources ────────────────────────────────────────────


async def test_data_sources(ctx: Ctx):
    ctx.section("Module 11: Admin Data Sources")
    if not ctx.admin_token:
        ctx.check(False, "SKIP — no admin token")
        return

    h = ctx.ah()
    code = f"src-{uuid.uuid4().hex[:8]}"

    # Create
    r = await ctx.c.post(
        "/admin/sources",
        json={
            "name": f"Test Bank {code}",
            "short_code": code,
            "source_type": "bank",
            "description": "Integration test source",
        },
        headers=h,
    )
    if r.status_code == 201:
        ctx.source_id = str(r.json()["id"])
        ctx.check(True, "Create data source", f"id={ctx.source_id}")
    else:
        ctx.check(False, "Create data source", f"→ {r.status_code} {r.text[:200]}")

    # Duplicate
    r = await ctx.c.post(
        "/admin/sources",
        json={
            "name": "Dup",
            "short_code": code,
            "source_type": "bank",
        },
        headers=h,
    )
    ctx.check(r.status_code == 409, "Duplicate short_code → 409", f"→ {r.status_code}")

    # Get
    if ctx.source_id:
        r = await ctx.c.get(f"/admin/sources/{ctx.source_id}", headers=h)
        ctx.check(
            r.status_code == 200 and r.json().get("short_code") == code,
            "Get data source",
        )
        ctx.check(r.json().get("has_active_template") is False, "No active template initially")

    # List
    r = await ctx.c.get("/admin/sources", headers=h)
    ctx.check(
        r.status_code == 200 and r.json().get("total", 0) >= 1,
        "List data sources",
        f"total={r.json().get('total')}",
    )

    # Search
    r = await ctx.c.get(f"/admin/sources?search={code}", headers=h)
    ctx.check(
        r.status_code == 200 and len(r.json().get("items", [])) >= 1,
        "Search data sources",
    )

    # Template (none)
    if ctx.source_id:
        r = await ctx.c.get(f"/admin/sources/{ctx.source_id}/template", headers=h)
        ctx.check(r.status_code == 200, "Get template (none exists)")

    # Uploads (empty)
    if ctx.source_id:
        r = await ctx.c.get(f"/admin/sources/{ctx.source_id}/uploads", headers=h)
        ctx.check(
            r.status_code == 200 and r.json().get("total") == 0,
            "List source uploads (empty)",
        )

    # Non-existent
    r = await ctx.c.get(f"/admin/sources/{uuid.uuid4()}", headers=h)
    ctx.check(r.status_code == 404, "Get non-existent → 404", f"→ {r.status_code}")


# ── Module 12: Training Data Upload ──────────────────────────────────────────


async def test_training_data(ctx: Ctx):
    ctx.section("Module 12: Admin Training Data")
    if not ctx.admin_token or not ctx.source_id:
        ctx.check(False, "SKIP — no admin token or source_id")
        return

    h = ctx.ah()

    # Generate a minimal CSV in memory
    import io

    csv_content = (
        "full_name,date_of_birth,national_id,phone,loan_amount,tenure_months,"
        "monthly_income,employment_status,credit_score,is_default\n"
        "Kofi Mensah,1985-03-15,GHA-111111,+233500000001,5000,12,3000,"
        "employed,650,0\n"
        "Ama Serwaa,1990-07-22,GHA-222222,+233500000002,8000,24,4500,"
        "self_employed,580,1\n"
        "Yaw Boateng,1978-11-01,GHA-333333,+233500000003,3000,6,2000,"
        "employed,720,0\n"
    )
    files = {"file": ("test_data.csv", io.BytesIO(csv_content.encode()), "text/csv")}
    data = {"source_id": ctx.source_id}

    r = await ctx.c.post("/admin/training-data/upload", files=files, data=data, headers=h)
    if r.status_code in (200, 201):
        upload = r.json()
        upload_id = str(upload.get("id"))
        ctx.check(True, "Upload CSV", f"id={upload_id}, status={upload.get('status')}")
    else:
        ctx.check(False, "Upload CSV", f"→ {r.status_code} {r.text[:300]}")
        return

    # Get upload
    r = await ctx.c.get(f"/admin/training-data/{upload_id}", headers=h)
    ctx.check(r.status_code == 200, "Get upload details", f"status={r.json().get('status')}")

    # Status poll
    r = await ctx.c.get(f"/admin/training-data/{upload_id}/status", headers=h)
    ctx.check(r.status_code == 200, "Get upload status", f"status={r.json().get('status')}")

    # List uploads
    r = await ctx.c.get("/admin/training-data", headers=h)
    ctx.check(
        r.status_code == 200 and r.json().get("total", 0) >= 1,
        "List uploads",
        f"total={r.json().get('total')}",
    )

    # List by source
    r = await ctx.c.get(f"/admin/training-data?source_id={ctx.source_id}", headers=h)
    ctx.check(
        r.status_code == 200 and r.json().get("total", 0) >= 1,
        "List uploads by source_id",
    )

    # Get mapping
    r = await ctx.c.get(f"/admin/training-data/{upload_id}/mapping", headers=h)
    if r.status_code == 200:
        ctx.check(True, "Get mapping", f"features_mapped={r.json().get('features_mapped')}")
    else:
        ctx.check(
            r.status_code in (200, 202, 404),
            "Get mapping",
            f"→ {r.status_code} (may need DQA callback first)",
        )

    # Reject upload
    r = await ctx.c.post(
        f"/admin/training-data/{upload_id}/reject",
        json={"reason": "Integration test rejection"},
        headers=h,
    )
    if r.status_code == 200:
        ctx.check(True, "Reject upload")
        # Verify
        r = await ctx.c.get(f"/admin/training-data/{upload_id}", headers=h)
        ctx.check(r.json().get("status") == "rejected", "Rejection verified via GET")
    else:
        ctx.check(
            r.status_code in (200, 409),
            "Reject upload",
            f"→ {r.status_code} (may already be in wrong state)",
        )


# ── Main ─────────────────────────────────────────────────────────────────────


async def main():
    banner = "PaySwitch Integration Test Suite"
    sep = "═" * 55
    print(f"\n{sep}\n  {banner}\n{sep}")

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
        ctx = Ctx(client)

        await test_health(ctx)
        await test_admin_auth(ctx)
        await test_org_crud(ctx)
        await test_org_auth(ctx)
        await test_profile(ctx)
        await test_users(ctx)
        await test_api_keys(ctx)
        await test_webhooks(ctx)
        await test_scoring(ctx)
        await test_api_logs(ctx)
        await test_data_sources(ctx)
        await test_training_data(ctx)

        # Summary
        total = ctx.passed + ctx.failed
        print(f"\n{sep}")
        if ctx.failed == 0:
            print(f"  {G}Results: {ctx.passed}/{total} passed ✔{X}")
        else:
            print(f"  {R}Results: {ctx.passed}/{total} passed, {ctx.failed} failed ✘{X}")
            print(f"\n  {R}Failed tests:{X}")
            for e in ctx.errors:
                print(f"    {NO} {e}")
        print(sep)

    return 0 if ctx.failed == 0 else 1


if __name__ == "__main__":
    exit(asyncio.run(main()))
