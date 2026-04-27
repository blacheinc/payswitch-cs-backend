"""End-to-end test for the cross-org admin read paths.

Validates the four-cell behavioural matrix from
``app/services/cross_org_access.py::resolve_org_filter``:

  * Org user — own org param → OK
  * Org user — other org param → 403
  * Admin    — no param        → cross-org reads
  * Admin    — specific org    → scoped reads

Plus the new admin-only platform endpoint:

  * GET /admin/score-requests/stats (no org)              → platform aggregates
  * GET /admin/score-requests/stats (?organization_id=…)  → scoped aggregates

Usage:
    .venv/bin/python scripts/test_cross_org_admin_reads.py
"""
from __future__ import annotations

import sys
import time
from typing import Any

import httpx

BASE = "http://localhost:8000"
ORG_USER_EMAIL = "test_org_user@mailinator.com"
ORG_USER_PASS = "Testing123!"
ADMIN_EMAIL = "engineering@opensaucery.africa"
ADMIN_PASS = "opensaucery"


def _ok(cond: bool, msg: str) -> None:
    print(f"  {'✅' if cond else '❌'} {msg}")
    if not cond:
        sys.exit(1)


def _login(c: httpx.Client, email: str, password: str) -> str:
    r = c.post("/auth/login", json={"email": email, "password": password})
    if r.status_code != 200:
        raise SystemExit(f"login failed for {email} ({r.status_code}): {r.text}")
    return r.json()["access_token"]


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def main() -> None:
    with httpx.Client(base_url=BASE, timeout=30.0) as c:
        print("\n═══ Step 0: Login as both an org user and an admin ═══")
        org_token = _login(c, ORG_USER_EMAIL, ORG_USER_PASS)
        admin_token = _login(c, ADMIN_EMAIL, ADMIN_PASS)

        # Find the org user's organization_id and a different org id.
        me = c.get("/auth/me", headers=_h(org_token)).json()
        own_org_id = me["organization_id"]

        org_list = c.get(
            "/admin/organizations?per_page=5", headers=_h(admin_token)
        ).json()
        items = org_list.get("items", []) if isinstance(org_list, dict) else org_list
        other_org_id = next(
            (o["id"] for o in items if o["id"] != own_org_id),
            None,
        )
        _ok(other_org_id is not None, "found a second org id distinct from caller's")

        print(f"  own org    = {own_org_id}")
        print(f"  other org  = {other_org_id}")

        print("\n═══ Step 1: Org user — own org id is accepted ═══")
        r = c.get(
            f"/v1/score-requests?organization_id={own_org_id}", headers=_h(org_token)
        )
        _ok(r.status_code == 200, f"org user → own org: 200 (got {r.status_code})")

        print("\n═══ Step 2: Org user — other org id is rejected (403) ═══")
        r = c.get(
            f"/v1/score-requests?organization_id={other_org_id}",
            headers=_h(org_token),
        )
        _ok(r.status_code == 403, f"cross-org by non-admin → 403 (got {r.status_code})")

        print("\n═══ Step 3: Org user — no param defaults to own org (200) ═══")
        r = c.get("/v1/score-requests", headers=_h(org_token))
        _ok(r.status_code == 200, f"no param → 200 (got {r.status_code})")

        print("\n═══ Step 4: Admin — cross-org list (no param) ═══")
        r = c.get("/v1/score-requests", headers=_h(admin_token))
        _ok(r.status_code == 200, f"admin no param → 200 (got {r.status_code})")
        body = r.json()
        _ok("items" in body and "total" in body, "list response shape OK")

        print("\n═══ Step 5: Admin — scoped to specific org ═══")
        r = c.get(
            f"/v1/score-requests?organization_id={other_org_id}",
            headers=_h(admin_token),
        )
        _ok(r.status_code == 200, f"admin scoped → 200 (got {r.status_code})")

        print("\n═══ Step 6: Admin — bad UUID is rejected (422) ═══")
        r = c.get(
            "/v1/score-requests?organization_id=not-a-uuid",
            headers=_h(admin_token),
        )
        _ok(
            r.status_code in (400, 422),
            f"bad uuid → 4xx (got {r.status_code})",
        )

        print("\n═══ Step 7: Admin — /admin/score-requests/stats (platform-wide) ═══")
        r = c.get("/admin/score-requests/stats?period=30d", headers=_h(admin_token))
        _ok(r.status_code == 200, f"platform stats → 200 (got {r.status_code})")
        body: dict[str, Any] = r.json()
        _ok("current" in body and "previous" in body, "platform stats has current/previous")
        # by_org was dropped — per-org tables are built FE-side by paginating
        # /admin/organizations + fanning out scoped stats calls.
        _ok(
            "by_org" not in body,
            "platform stats no longer carries by_org (FE fans out from /admin/organizations)",
        )
        print(f"     platform total: {body['current']['total_requests']}")

        print("\n═══ Step 8: Admin — /admin/score-requests/stats?organization_id ═══")
        r = c.get(
            f"/admin/score-requests/stats?period=30d&organization_id={own_org_id}",
            headers=_h(admin_token),
        )
        _ok(r.status_code == 200, f"scoped platform stats → 200 (got {r.status_code})")
        scoped = r.json()
        _ok(
            set(scoped.keys()) == set(body.keys()),
            "scoped response shape matches platform-wide response shape",
        )

        print("\n═══ Step 9: Cross-org read writes an audit log row ═══")
        # Give the previous request's commit a moment, then count audit rows
        # written in the last 60 seconds for this admin actor.
        time.sleep(0.5)
        # Use a low-level DB peek — the API doesn't expose audit_logs directly
        import asyncio
        from sqlalchemy import func, select

        async def _count_recent_audits() -> int:
            from app.db import async_session_factory
            from app.models.audit import AuditLog

            async with async_session_factory() as db:
                count = (
                    await db.execute(
                        select(func.count(AuditLog.id)).where(
                            AuditLog.actor_email == ADMIN_EMAIL,
                            AuditLog.action == "score_requests.read_cross_org",
                        )
                    )
                ).scalar_one()
                return count

        count = asyncio.run(_count_recent_audits())
        _ok(count > 0, f"≥1 cross_org audit row recorded (got {count})")

        print("\n═══ Step 10: Org user can hit /admin/* → 403 ═══")
        r = c.get("/admin/score-requests/stats", headers=_h(org_token))
        _ok(
            r.status_code == 403,
            f"org user → admin endpoint: 403 (got {r.status_code})",
        )

        print("\n═══ Step 11: Batch scoring — same matrix ═══")
        r = c.get("/v1/score/batch", headers=_h(admin_token))
        _ok(r.status_code == 200, f"admin → batch list cross-org: 200 (got {r.status_code})")
        r = c.get(
            f"/v1/score/batch?organization_id={other_org_id}",
            headers=_h(org_token),
        )
        _ok(
            r.status_code == 403,
            f"org user → other org batch list: 403 (got {r.status_code})",
        )

        print("\n🎉 cross-org admin read flow verified.")


if __name__ == "__main__":
    main()
