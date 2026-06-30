import asyncio
import os
import sys
import uuid
from datetime import datetime

import httpx

# Add project root to path for imports
sys.path.append(os.getcwd())

# Configuration
BASE_URL = os.environ.get("TEST_API_BASE_URL", "http://localhost:8000")
ADMIN_EMAIL = os.environ.get("TEST_ADMIN_EMAIL", "")
ADMIN_PASS = os.environ.get("TEST_ADMIN_PASSWORD", "")

# ANSI Colors
C_GREEN = "\033[92m"
C_RED = "\033[91m"
C_BLUE = "\033[94m"
C_YELLOW = "\033[93m"
C_RESET = "\033[0m"

CHECK = f"{C_GREEN}✔{C_RESET}"
FAIL = f"{C_RED}✘{C_RESET}"
INFO = f"{C_BLUE}ℹ{C_RESET}"


async def test_health(client: httpx.AsyncClient):
    print(f"\n{C_BLUE}── Testing Health ──────────────────────────────{C_RESET}")
    try:
        resp = await client.get("/health")
        if resp.status_code == 200:
            print(f"{CHECK} GET /health ({resp.json()['status']})")
        else:
            print(f"{FAIL} GET /health: {resp.status_code}")
    except Exception as e:
        print(f"{FAIL} GET /health error: {e}")


async def test_admin_login(client: httpx.AsyncClient) -> str | None:
    print(f"\n{C_BLUE}── Testing Admin Auth ──────────────────────────{C_RESET}")
    try:
        payload = {"email": ADMIN_EMAIL, "password": ADMIN_PASS}
        resp = await client.post("/auth/login", json=payload)

        if resp.status_code == 200:
            token = resp.json()["access_token"]
            print(f"{CHECK} Admin Login Success")
            return token
        else:
            print(f"{FAIL} Admin Login Failed: {resp.status_code} {resp.text}")
            return None
    except Exception as e:
        print(f"{FAIL} Admin Login Error: {e}")
        return None


async def test_admin_orgs(client: httpx.AsyncClient, token: str) -> str | None:
    print(f"\n{C_BLUE}── Testing Admin Org Management ────────────────{C_RESET}")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. List Orgs
    try:
        resp = await client.get("/admin/organizations", headers=headers)
        if resp.status_code == 200:
            count = resp.json()["total"]
            print(f"{CHECK} List Orgs (Total: {count})")
        else:
            print(f"{FAIL} List Orgs Failed: {resp.status_code}")
    except Exception as e:
        print(f"{FAIL} List Orgs Error: {e}")

    # 2. Create Org
    org_name = f"Test Org {uuid.uuid4().hex[:6]}"
    slug = org_name.lower().replace(" ", "-")  # Short code regex: ^[a-z0-9_-]+$
    payload = {"name": org_name, "short_name": slug, "industry_type": "fintech"}
    try:
        resp = await client.post("/admin/organizations", json=payload, headers=headers)
        if resp.status_code in [200, 201]:
            data = resp.json()
            org_id = data["id"]
            print(f"{CHECK} Create Org '{org_name}' (ID: {org_id})")

            # Activate Org
            resp = await client.post(f"/admin/organizations/{org_id}/activate", headers=headers)
            if resp.status_code == 200:
                print(f"{CHECK} Activated Org")
            else:
                print(f"{FAIL} Activate Org Failed: {resp.status_code} {resp.text}")

            return org_id
        else:
            print(f"{FAIL} Create Org Failed: {resp.status_code} {resp.text}")
            return None
    except Exception as e:
        print(f"{FAIL} Create Org Error: {e}")
        return None


async def test_admin_sources_and_upload(client: httpx.AsyncClient, token: str):
    print(f"\n{C_BLUE}── Testing Data Sources & Upload ───────────────{C_RESET}")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. List Sources
    resp = await client.get("/admin/sources", headers=headers)
    if resp.status_code == 200:
        print(f"{CHECK} List Sources (Total: {resp.json()['total']})")
    else:
        print(f"{FAIL} List Sources Failed: {resp.status_code}")

    # 2. Create Source
    source_name = f"Test Source {uuid.uuid4().hex[:6]}"
    payload = {
        "name": source_name,
        "description": "E2E Test Source",
        "data_type": "historical_loans",
    }
    resp = await client.post("/admin/sources", json=payload, headers=headers)
    source_id = None
    if resp.status_code == 201:
        source_id = resp.json()["id"]
        print(f"{CHECK} Created Source '{source_name}' (ID: {source_id})")
    else:
        print(f"{FAIL} Create Source Failed: {resp.status_code} {resp.text}")

    # 3. Upload File
    if source_id:
        file_content = b"customer_id,loan_amount,status\n1,5000,paid\n2,3000,defaulted\n"
        files = {"file": ("test_data.csv", file_content, "text/csv")}
        data = {"source_id": source_id}

        # httpx multipart file upload
        resp = await client.post(
            "/admin/training-data/upload", headers=headers, data=data, files=files
        )

        if resp.status_code == 201:
            upload_id = resp.json()["id"]
            print(f"{CHECK} Uploaded Training Data (Upload ID: {upload_id})")

            # 4. Check status
            resp = await client.get(f"/admin/training-data/{upload_id}", headers=headers)
            if resp.status_code == 200:
                print(f"{CHECK} Upload Status: {resp.json()['status']}")
            else:
                print(f"{FAIL} Get Upload Status Failed: {resp.status_code}")
        else:
            print(f"{FAIL} Upload Training Data Failed: {resp.status_code} {resp.text}")


async def seed_org_user(org_id: str, email: str, password: str):
    print(f"\n{C_BLUE}── Seeding Org User ────────────────────────────{C_RESET}")

    try:
        from sqlalchemy import update

        from app.core.security import hash_password
        from app.db import async_session_factory
        from app.models.organization import Organization, OrgStatus
        from app.models.user import OrgRoleLabel, OrgUser, OrgUserStatus

        async with async_session_factory() as session:
            # Activate the organization so API tests work
            await session.execute(
                update(Organization)
                .where(Organization.id == org_id)
                .values(status=OrgStatus.ACTIVE)
            )

            new_user = OrgUser(
                email=email,
                name="Test API User",
                password_hash=hash_password(password),
                organization_id=org_id,
                role_label=OrgRoleLabel.ADMIN,
                status=OrgUserStatus.ACTIVE,
            )
            session.add(new_user)
            await session.commit()
            print(f"{CHECK} Seeded user '{email}' for Org {org_id}")
    except Exception as e:
        print(f"{FAIL} Seeding Failed: {e}")


async def test_org_login(client: httpx.AsyncClient, email: str, password: str) -> str | None:
    print(f"\n{C_BLUE}── Testing Org Auth ────────────────────────────{C_RESET}")
    try:
        payload = {"email": email, "password": password}
        resp = await client.post("/auth/login", json=payload)

        if resp.status_code == 200:
            token = resp.json()["access_token"]
            print(f"{CHECK} Org Login Success")
            return token
        else:
            print(f"{FAIL} Org Login Failed: {resp.status_code} {resp.text}")
            return None
    except Exception as e:
        print(f"{FAIL} Org Login Error: {e}")
        return None


async def test_org_portal(client: httpx.AsyncClient, token: str) -> str | None:
    print(f"\n{C_BLUE}── Testing Org Portal ──────────────────────────{C_RESET}")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Profile
    resp = await client.get("/org/profile", headers=headers)
    if resp.status_code == 200:
        print(f"{CHECK} Get Profile: {resp.json()['name']}")
    else:
        print(f"{FAIL} Get Profile Failed: {resp.status_code}")

    # 2. Get/Generate API Key
    api_key_val = None
    resp = await client.get("/org/api-keys", headers=headers)
    if resp.status_code == 200:
        keys = resp.json()
        print(f"{CHECK} List API Keys (Found: {len(keys)})")

        if not keys:
            # Create one
            payload = {"name": "Test Key", "environment": "sandbox"}
            resp = await client.post("/org/api-keys", json=payload, headers=headers)
            if resp.status_code == 201:
                key_val = resp.json()["key"]
                print(f"{CHECK} Generated API Key: {key_val[:10]}...")
                api_key_val = key_val
            else:
                print(f"{FAIL} Generate API Key Failed: {resp.status_code} {resp.text}")
        else:
            # We can't get the key value again for existing keys (masked).
            # But we can create a new one for testing scoring.
            payload = {
                "name": f"Test Key {uuid.uuid4().hex[:4]}",
                "environment": "sandbox",
            }
            resp = await client.post("/org/api-keys", json=payload, headers=headers)
            if resp.status_code == 201:
                api_key_val = resp.json()["key"]
                print(f"{CHECK} Generated New API Key for Scoring")

    else:
        print(f"{FAIL} List API Keys Failed: {resp.status_code}")

    # 3. List API Logs
    resp = await client.get("/org/api-logs", headers=headers)
    if resp.status_code == 200:
        print(f"{CHECK} List API Logs (Total: {resp.json()['total']})")
    else:
        print(f"{FAIL} List API Logs Failed: {resp.status_code}")

    return api_key_val


async def test_scoring(client: httpx.AsyncClient, token: str, api_key: str | None):
    print(f"\n{C_BLUE}── Testing Scoring ─────────────────────────────{C_RESET}")
    headers = {"Authorization": f"Bearer {token}"}

    payload = {
        "applicant": {
            "full_name": "Kwame Nkrumah",
            "date_of_birth": "1980-01-01",
            "national_id_type": "ghana_card",
            "national_id_number": "GHA-123456789-0",
            "phone": "+233541234567",
        },
        "loan_request": {"amount": 5000, "purpose": "business", "tenure_months": 12},
        "consent": {"consent_date": datetim_to_date_str(datetime.utcnow())},
    }

    # Try with Bearer
    print(f"\n{INFO} Testing with Bearer Token...")
    resp = await client.post("/v1/score-requests", json=payload, headers=headers)
    if resp.status_code in [200, 201]:
        print(f"{CHECK} Create Score Request (Bearer)")
    else:
        print(f"{FAIL} Create Score Request (Bearer) Failed: {resp.status_code} {resp.text}")

    # Try with API Key if available
    if api_key:
        print(f"\n{INFO} Testing with API Key...")
        headers_key = {"X-API-Key": api_key}
        # modify payload slightly to avoid duplicate check?
        payload["applicant"]["national_id_number"] = f"GHA-{uuid.uuid4().hex[:9]}-0"

        resp = await client.post("/v1/score-requests", json=payload, headers=headers_key)
        if resp.status_code in [200, 201]:
            print(f"{CHECK} Create Score Request (API Key)")
        else:
            print(f"{FAIL} Create Score Request (API Key) Failed: {resp.status_code} {resp.text}")


def datetim_to_date_str(dt):
    return dt.strftime("%Y-%m-%d")


async def main():
    print(f"{INFO} Starting API Test Script...")
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        await test_health(client)

        admin_token = await test_admin_login(client)
        if not admin_token:
            return

        org_id = await test_admin_orgs(client, admin_token)
        await test_admin_sources_and_upload(client, admin_token)

        if org_id:
            user_email = f"test_{uuid.uuid4().hex[:6]}@example.com"
            user_pass = "Test@1234"
            await seed_org_user(org_id, user_email, user_pass)

            org_token = await test_org_login(client, user_email, user_pass)
            if org_token:
                api_key = await test_org_portal(client, org_token)
                await test_scoring(client, org_token, api_key)


if __name__ == "__main__":
    asyncio.run(main())
