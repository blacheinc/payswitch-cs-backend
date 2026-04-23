"""End-to-end test for /admin/admins CRUD.

Logs in as the seeded SUPER_ADMIN, exercises the full lifecycle against a
throwaway invitee (invite → list → get → update role → suspend → activate
→ delete), then asserts the last-SUPER_ADMIN guard refuses self-demotion.

Usage:
    .venv/bin/python scripts/test_admin_admins_flow.py
"""
from __future__ import annotations

import asyncio
import sys
import uuid

import httpx

BASE = "http://localhost:8000"
SUPER_ADMIN_EMAIL = "engineering@opensaucery.africa"
SUPER_ADMIN_PASS = "opensaucery"


def _ok(cond: bool, msg: str) -> None:
    mark = "✅" if cond else "❌"
    print(f"  {mark} {msg}")
    if not cond:
        sys.exit(1)


async def _login(c: httpx.AsyncClient) -> str:
    r = await c.post(
        "/auth/login",
        json={"email": SUPER_ADMIN_EMAIL, "password": SUPER_ADMIN_PASS},
    )
    if r.status_code != 200:
        raise SystemExit(f"Admin login failed ({r.status_code}): {r.text}")
    return r.json()["access_token"]


async def _fetch_platform_role_id(c: httpx.AsyncClient, headers: dict, name: str) -> str:
    """Resolve a platform role by name via /v1/roles so we can assign it."""
    r = await c.get("/v1/roles", headers=headers, params={"scope": "platform"})
    if r.status_code != 200:
        raise SystemExit(f"List roles failed ({r.status_code}): {r.text}")
    body = r.json()
    items = body.get("items") if isinstance(body, dict) else body
    for item in items or []:
        if item.get("name") == name:
            return item["id"]
    raise SystemExit(f"Platform role {name!r} not found in /v1/roles response: {body}")


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as c:
        print("\n═══ Step 0: Login as SUPER_ADMIN ═══")
        token = await _login(c)
        headers = {"Authorization": f"Bearer {token}"}
        print(f"  logged in as {SUPER_ADMIN_EMAIL}")

        print("\n═══ Step 1: Resolve platform role IDs ═══")
        super_admin_role_id = await _fetch_platform_role_id(c, headers, "SUPER_ADMIN")
        risk_role_id = await _fetch_platform_role_id(c, headers, "RISK_ANALYST")
        print(f"  SUPER_ADMIN     = {super_admin_role_id}")
        print(f"  RISK_ANALYST    = {risk_role_id}")

        print("\n═══ Step 2: Invite a new RISK_ANALYST admin ═══")
        invite_email = f"risk_{uuid.uuid4().hex[:6]}@mailinator.com"
        r = await c.post(
            "/admin/admins",
            headers=headers,
            json={
                "email": invite_email,
                "name": "Risk Analyst Trainee",
                "role_id": risk_role_id,
                "callback_url": "http://localhost:3001/login",
            },
        )
        _ok(r.status_code == 201, f"invite → 201 (got {r.status_code}: {r.text[:200]})")
        invitee = r.json()
        invitee_id = invitee["id"]
        _ok(invitee["role_name"] == "RISK_ANALYST", "invitee role_name=RISK_ANALYST")
        _ok(invitee["status"] == "active", "invitee status=active")

        print("\n═══ Step 3: List admins and ensure the invitee appears ═══")
        r = await c.get("/admin/admins", headers=headers)
        _ok(r.status_code == 200, f"list → 200 (got {r.status_code})")
        ids = [a["id"] for a in r.json()["items"]]
        _ok(invitee_id in ids, "invitee appears in list")

        print("\n═══ Step 4: GET individual admin ═══")
        r = await c.get(f"/admin/admins/{invitee_id}", headers=headers)
        _ok(r.status_code == 200, f"get → 200 (got {r.status_code})")
        _ok(r.json()["email"] == invite_email, "get returns correct email")

        print("\n═══ Step 5: PATCH name + role to MODEL_OPS_ENGINEER ═══")
        model_ops_role_id = await _fetch_platform_role_id(
            c, headers, "MODEL_OPS_ENGINEER"
        )
        r = await c.patch(
            f"/admin/admins/{invitee_id}",
            headers=headers,
            json={"name": "Promoted ModelOps", "role_id": model_ops_role_id},
        )
        _ok(r.status_code == 200, f"patch → 200 (got {r.status_code}: {r.text[:200]})")
        _ok(r.json()["name"] == "Promoted ModelOps", "name updated")
        _ok(
            r.json()["role_name"] == "MODEL_OPS_ENGINEER",
            "role_name updated to MODEL_OPS_ENGINEER",
        )

        print("\n═══ Step 6: Suspend then activate invitee ═══")
        r = await c.post(f"/admin/admins/{invitee_id}/suspend", headers=headers)
        _ok(r.status_code == 200, f"suspend → 200 (got {r.status_code}: {r.text[:200]})")
        r = await c.get(f"/admin/admins/{invitee_id}", headers=headers)
        _ok(r.json()["status"] == "deactivated", "status=deactivated after suspend")

        r = await c.post(f"/admin/admins/{invitee_id}/activate", headers=headers)
        _ok(r.status_code == 200, f"activate → 200 (got {r.status_code})")
        r = await c.get(f"/admin/admins/{invitee_id}", headers=headers)
        _ok(r.json()["status"] == "active", "status=active after activate")

        print("\n═══ Step 7: Last-SUPER_ADMIN guard should refuse self-demotion ═══")
        # Find my own id
        r = await c.get("/auth/me", headers=headers)
        my_id = r.json()["id"]

        # How many other ACTIVE SUPER_ADMINs are there?  If >1, the guard
        # cannot fire for this action — we skip the assertion.
        r = await c.get("/admin/admins", headers=headers)
        active_supers = [
            a
            for a in r.json()["items"]
            if a["role_name"] == "SUPER_ADMIN" and a["status"] == "active"
        ]
        if len(active_supers) == 1:
            r = await c.patch(
                f"/admin/admins/{my_id}",
                headers=headers,
                json={"role_id": risk_role_id},
            )
            _ok(
                r.status_code == 409,
                f"self-demote blocked with 409 (got {r.status_code}: {r.text[:200]})",
            )
        else:
            print(
                f"  ⚠️  {len(active_supers)} active SUPER_ADMINs present — "
                "skipping self-demote guard check"
            )

        print("\n═══ Step 8: Self-suspend is refused ═══")
        r = await c.post(f"/admin/admins/{my_id}/suspend", headers=headers)
        _ok(
            r.status_code == 409,
            f"self-suspend → 409 (got {r.status_code}: {r.text[:200]})",
        )

        print("\n═══ Step 9: DELETE invitee (soft) ═══")
        r = await c.delete(f"/admin/admins/{invitee_id}", headers=headers)
        _ok(r.status_code == 200, f"delete → 200 (got {r.status_code}: {r.text[:200]})")

        r = await c.get(f"/admin/admins/{invitee_id}", headers=headers)
        _ok(r.status_code == 200, "admin row retained (soft delete)")
        _ok(r.json()["status"] == "deactivated", "status=deactivated after delete")
        _ok(r.json()["role_id"] is None, "RBAC role detached after delete")

        print("\n🎉 /admin/admins CRUD flow verified.")


if __name__ == "__main__":
    asyncio.run(main())
