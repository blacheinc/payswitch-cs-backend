"""End-to-end test: bureau-lookup → score-request → fetch by ID."""
import asyncio
import json
import httpx

BASE = "http://localhost:8000"
ADMIN_EMAIL = "engineering@opensaucery.africa"
ADMIN_PASS = "opensaucery"


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as c:

        # ── Step 0: Login as admin ──────────────────────────────────
        print("\n═══ Step 0: Login ═══")
        r = await c.post("/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASS})
        assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
        login_data = r.json()
        token = login_data["access_token"]
        print(f"✅ Logged in (user_type={login_data.get('user_type')})")

        # Check if this is admin — we need an org user token for scoring.
        # Admin tokens don't have org_id. Let's check what orgs exist and find/create an org user.
        headers = {"Authorization": f"Bearer {token}"}

        if login_data.get("user_type") == "admin":
            # List orgs
            r = await c.get("/admin/organizations", headers=headers)
            orgs = r.json()
            print(f"   Orgs: {orgs.get('total', 0)} found")

            if orgs.get("total", 0) > 0:
                org_id = orgs["items"][0]["id"]
                org_name = orgs["items"][0]["name"]
                print(f"   Using org: {org_name} ({org_id})")

                # Try to find an existing org user or create one for testing
                r = await c.get(f"/admin/organizations/{org_id}/users", headers=headers)
                if r.status_code == 200:
                    users = r.json()
                    print(f"   Org users: {users.get('total', len(users) if isinstance(users, list) else 0)}")
            else:
                print("❌ No organizations found — cannot test scoring")
                return

            # We need an org user token. Let's create a temp user and login.
            import uuid
            from app.db import async_session_factory
            from app.models.user import OrgUser, OrgRoleLabel, OrgUserStatus
            from app.core.security import hash_password

            test_email = f"scoretest_{uuid.uuid4().hex[:6]}@test.com"
            test_pass = "Test@1234"

            async with async_session_factory() as session:
                async with session.begin():
                    new_user = OrgUser(
                        email=test_email,
                        name="Score Test User",
                        password_hash=hash_password(test_pass),
                        organization_id=org_id,
                        role_label=OrgRoleLabel.ADMIN,
                        status=OrgUserStatus.ACTIVE,
                    )
                    session.add(new_user)
            print(f"✅ Created temp org user: {test_email}")

            r = await c.post("/auth/login", json={"email": test_email, "password": test_pass})
            assert r.status_code == 200, f"Org login failed: {r.status_code} {r.text}"
            token = r.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            print("✅ Logged in as org user")

        # ── Step 1: Bureau Lookup ───────────────────────────────────
        print("\n═══ Step 1: Bureau Lookup ═══")
        bureau_payload = {
            "full_name": "John",
            "date_of_birth": "1990-05-15",
            "identification": "3611003033",
            "phone_number": "0244123456",
            "enquiry_reason": "Application for credit by a borrower",
        }
        r = await c.post("/v1/bureau-lookup", json=bureau_payload, headers=headers)
        print(f"   Status: {r.status_code}")
        bureau_resp = r.json()
        print(f"   Bureau hit status: {bureau_resp.get('bureau_hit_status')}")
        print(f"   Consumer ID: {bureau_resp.get('consumer_id')}")
        if bureau_resp.get("features"):
            feats = bureau_resp["features"]
            print(f"   identity_verified: {feats.get('identity_verified')}")
            print(f"   total_arrear_amount_ghs: {feats.get('total_arrear_amount_ghs')}")
            print(f"   has_judgement: {feats.get('has_judgement')}")
        if bureau_resp.get("credit_summary"):
            cs = bureau_resp["credit_summary"]
            print(f"   credit_summary.total_judgements: {cs.get('total_judgements')}")
            print(f"   credit_summary.delinquency_rating: {cs.get('delinquency_rating')}")

        # ── Step 2: Score Request ───────────────────────────────────
        print("\n═══ Step 2: Score Request ═══")
        score_payload = {
            "reference_id": "REF-TEST-E2E",
            "applicant": {
                "full_name": "John",
                "date_of_birth": "1990-05-15",
                "national_id_number": "3611003033",
                "phone": "0244123456",
            },
            "loan_request": {
                "amount": 76000,
                "tenure_months": 12,
                "purpose": "housing",
            },
            "bureau_data": {
                "consumer_id": bureau_resp.get("consumer_id", "1589072"),
                "bureau_hit_status": bureau_resp.get("bureau_hit_status", "HIT"),
                "product_source": (bureau_resp.get("metadata") or {}).get("product_source"),
                "credit_summary": bureau_resp.get("credit_summary"),
                "credit_accounts": bureau_resp.get("credit_accounts"),
                "features": bureau_resp.get("features"),
            },
            "metadata": {"channel": "web_portal"},
        }
        r = await c.post("/v1/score-requests", json=score_payload, headers=headers)
        print(f"   Status: {r.status_code}")
        score_resp = r.json()
        print(f"   Response:\n{json.dumps(score_resp, indent=2)}")

        request_id = score_resp.get("request_id")
        if not request_id:
            print("❌ No request_id in response")
            return

        # ── Step 3: Fetch Score Request by ID ───────────────────────
        print(f"\n═══ Step 3: GET /v1/score-requests/{request_id} ═══")
        r = await c.get(f"/v1/score-requests/{request_id}", headers=headers)
        print(f"   Status: {r.status_code}")
        get_resp = r.json()
        print(f"   Response:\n{json.dumps(get_resp, indent=2)}")

        # ── Step 4: Fetch Scoring Result ────────────────────────────
        print(f"\n═══ Step 4: GET /v1/score-requests/{request_id}/scoring-result ═══")
        r = await c.get(f"/v1/score-requests/{request_id}/scoring-result", headers=headers)
        print(f"   Status: {r.status_code}")
        sr_resp = r.json()
        print(f"   Response:\n{json.dumps(sr_resp, indent=2)}")


if __name__ == "__main__":
    asyncio.run(main())
