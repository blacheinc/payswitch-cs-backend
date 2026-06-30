import asyncio
import os
import httpx

API_BASE_URL = os.environ.get("TEST_API_BASE_URL", "http://localhost:8000")
ADMIN_EMAIL = os.environ.get("TEST_ADMIN_EMAIL", "")
ADMIN_PASSWORD = os.environ.get("TEST_ADMIN_PASSWORD", "")

async def test_bureau_endpoint():
    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=60.0) as client:
        print("1. Logging in as admin...")
        login_resp = await client.post(
            "/auth/login",
            json={
                "email": ADMIN_EMAIL,
                "password": ADMIN_PASSWORD,
            }
        )
        
        if login_resp.status_code != 200:
            print(f"❌ Login failed: {login_resp.status_code}")
            print(login_resp.text)
            return
            
        token = login_resp.json().get("access_token")
        print(f"✅ Login successful, got token.")

        print("\n2. Testing /v1/bureau-lookup...")
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "full_name": "John",
            "date_of_birth": "1990-05-15",
            "identification": "3611003033",
            "phone_number": "0244123456",
            "enquiry_reason": "Application for credit by a borrower"
        }
        
        bureau_resp = await client.post(
            "/v1/bureau-lookup",
            json=payload,
            headers=headers
        )
        
        print(f"\nStatus Code: {bureau_resp.status_code}")
        
        try:
            import json
            print("\nResponse:")
            print(json.dumps(bureau_resp.json(), indent=2))
        except Exception:
            print("\nRaw Response:")
            print(bureau_resp.text)

if __name__ == "__main__":
    asyncio.run(test_bureau_endpoint())
