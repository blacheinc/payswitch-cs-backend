"""
End-to-end script: fetch real XDS data via bureau-lookup, then push the
exact DE payload to the Azure Function inference endpoint.

Usage:
    python3 scripts/test_de_trigger.py
"""

import asyncio
import json
import os
import uuid

import httpx

API_BASE_URL = os.environ.get("TEST_API_BASE_URL", "http://localhost:8000")
DE_INFERENCE_URL = os.environ.get("DE_INFERENCE_URL", "")
DE_INFERENCE_CODE = os.environ.get("DE_INFERENCE_CODE", "")
ADMIN_EMAIL = os.environ.get("TEST_ADMIN_EMAIL", "")
ADMIN_PASSWORD = os.environ.get("TEST_ADMIN_PASSWORD", "")


async def main():
    # ── Step 1: Login & fetch real XDS data via bureau-lookup ─────────────
    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=60.0) as client:
        print("1. Logging in as admin...")
        login_resp = await client.post(
            "/auth/login",
            json={
                "email": ADMIN_EMAIL,
                "password": ADMIN_PASSWORD,
            },
        )
        if login_resp.status_code != 200:
            print(f"❌ Login failed: {login_resp.status_code}")
            print(login_resp.text)
            return

        token = login_resp.json().get("access_token")
        headers = {"Authorization": f"Bearer {token}"}

        print("\n2. Fetching real XDS data via /v1/bureau-lookup...")
        bureau_resp = await client.post(
            "/v1/bureau-lookup",
            json={
                "full_name": "John",
                "date_of_birth": "1990-05-15",
                "identification": "3611003033",
                "phone_number": "0244123456",
                "enquiry_reason": "Application for credit by a borrower",
            },
            headers=headers,
        )

        if bureau_resp.status_code != 200:
            print(f"❌ Bureau lookup failed: {bureau_resp.status_code}")
            print(bureau_resp.text)
            return

        bureau_data = bureau_resp.json()
        consumer_id = bureau_data.get("consumer_id")
        print(f"✅ Bureau lookup succeeded. consumer_id={consumer_id}")
        print(f"   Hit status: {bureau_data.get('bureau_hit_status')}")

    # ── Step 2: Build the DE payload ─────────────────────────────────────
    request_id = f"TEST-{uuid.uuid4().hex[:8].upper()}"

    de_payload = {
        "request_id": request_id,
        "data_source_id": request_id,
        "source_system": "xds",
        "models_to_run": ["all"],
        "xds_payload": {
            "consumer_full_report_45": {
                "response": {"statusCode": 200, "message": "Success"},
                "personalDetailsSummary": bureau_data.get("personal_details"),
                "creditAccountSummary": bureau_data.get("credit_summary"),
                "creditAgreementSummary": bureau_data.get("credit_accounts"),
            },
            "consumer_mobile_report_49": None,
        },
    }

    # ── Step 3: Save payload to file & POST to DE Azure Function ────────
    payload_path = os.path.join(os.path.dirname(__file__), "de_payload.json")
    with open(payload_path, "w") as f:
        json.dump(de_payload, f, indent=2)
    print(f"\n3. Saved payload to {payload_path}")

    print("\n4. Posting payload to DE Azure Function...")
    print(f"   URL: {DE_INFERENCE_URL}")
    print(f"   request_id: {request_id}")
    print(f"   Payload size: {len(json.dumps(de_payload))} bytes")

    try:
        full_url = (
            f"{DE_INFERENCE_URL}?code={DE_INFERENCE_CODE}"
            if DE_INFERENCE_CODE
            else DE_INFERENCE_URL
        )
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(full_url, json=de_payload)

        print(f"\n   Response: HTTP {resp.status_code}")
        if resp.status_code in (200, 201, 202):
            print("✅ Successfully triggered DE inference!")
            try:
                print(json.dumps(resp.json(), indent=2))
            except Exception:
                print(resp.text)
        else:
            print("❌ DE inference call failed")
            print(f"   {resp.text}")
    except Exception as e:
        print(f"\n❌ Could not reach DE Azure Function: {e}")
        print("   The payload was built successfully — dumping to stdout:\n")
        print(json.dumps(de_payload, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
