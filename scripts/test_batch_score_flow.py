"""End-to-end test for the batch scoring flow.

Submits a batch with 3 items:
  * items 0 & 1: valid payloads using deterministic demo bureau records. In
    DEMO_SIMULATION_MODE these complete without calling client XDS/DE services.
  * item 2: valid API payload with an unknown demo identification, yielding a
    terminal ``failed`` item with error_code="bureau_failed".

Usage:
    .venv/bin/python scripts/test_batch_score_flow.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

import httpx

BASE = os.environ.get("TEST_API_BASE_URL", "http://localhost:8000")
ORG_USER_EMAIL = os.environ.get("TEST_ORG_EMAIL", "")
ORG_USER_PASS = os.environ.get("TEST_ORG_PASSWORD", "")

# Max seconds to wait for the job to finish. XDS + DE calls can be slow.
POLL_TIMEOUT_S = 180
POLL_INTERVAL_S = 3


async def _login(c: httpx.AsyncClient) -> str:
    r = await c.post("/auth/login", json={"email": ORG_USER_EMAIL, "password": ORG_USER_PASS})
    if r.status_code != 200:
        raise SystemExit(f"Login failed ({r.status_code}): {r.text}")
    token = r.json()["access_token"]
    print(f"  logged in as {ORG_USER_EMAIL}")
    return token


async def _submit_batch(c: httpx.AsyncClient, headers: dict) -> str:
    """Submit two deterministic successes and one deterministic failure."""
    payload = {
        "items": [
            {
                "full_name": "Kofi Owusu",
                "date_of_birth": "1966-12-15",
                "identification": "FUW3142495073",
                "phone_number": "0244123456",
                "enquiry_reason": "Application for credit by a borrower",
            },
            {
                "full_name": "Fiifi Quaye",
                "date_of_birth": "1961-05-03",
                "identification": "NNG3588016011",
                "phone_number": "0244123457",
                "enquiry_reason": "Application for credit by a borrower",
            },
            {
                "full_name": "Unknown Applicant",
                "date_of_birth": "1990-05-15",
                "identification": "UNKNOWN-DEMO-ID",
                "phone_number": "0244000000",
                "enquiry_reason": "Application for credit by a borrower",
            },
        ]
    }
    r = await c.post("/v1/score/batch", json=payload, headers=headers)
    if r.status_code != 202:
        raise SystemExit(f"Batch submit failed ({r.status_code}): {r.text}")
    body = r.json()
    job_id = body["job_id"]
    print(f"  submitted {job_id} (total={body['total']})")
    return job_id


async def _poll_until_done(c: httpx.AsyncClient, headers: dict, job_id: str) -> dict:
    deadline = time.monotonic() + POLL_TIMEOUT_S
    last_status = None
    while time.monotonic() < deadline:
        r = await c.get(f"/v1/score/batch/{job_id}", headers=headers)
        if r.status_code != 200:
            raise SystemExit(f"Status poll failed ({r.status_code}): {r.text}")
        body = r.json()
        snapshot = (
            body["status"],
            body["progress_pct"],
            tuple(sorted(body["items"].items())),
        )
        if snapshot != last_status:
            last_status = snapshot
            print(f"  [{body['status']}] progress={body['progress_pct']}% items={body['items']}")
        if body["status"] in ("completed", "failed", "cancelled"):
            return body
        await asyncio.sleep(POLL_INTERVAL_S)
    raise SystemExit(f"Timed out after {POLL_TIMEOUT_S}s waiting for {job_id}")


async def _fetch_results(c: httpx.AsyncClient, headers: dict, job_id: str) -> list[dict]:
    r = await c.get(f"/v1/score/batch/{job_id}/results", headers=headers)
    if r.status_code != 200:
        raise SystemExit(f"Results fetch failed ({r.status_code}): {r.text}")
    return r.json()["items"]


def _assert(cond: bool, msg: str) -> None:
    marker = "✅" if cond else "❌"
    print(f"  {marker} {msg}")
    if not cond:
        sys.exit(1)


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as c:
        print("\n═══ Step 0: Login ═══")
        token = await _login(c)
        headers = {"Authorization": f"Bearer {token}"}

        print("\n═══ Step 1: Submit batch of 3 items ═══")
        job_id = await _submit_batch(c, headers)

        print("\n═══ Step 2: Poll job status until terminal ═══")
        final = await _poll_until_done(c, headers, job_id)

        print("\n═══ Step 3: Fetch per-item results ═══")
        items = await _fetch_results(c, headers, job_id)
        for it in sorted(items, key=lambda x: x["index"]):
            print(
                f"  item[{it['index']}] status={it['status']:<9} "
                f"tracking_id={it.get('score_tracking_id') or '-'} "
                f"error={it.get('error_code') or '-'}"
            )

        print("\n═══ Step 4: Assertions ═══")
        by_idx = {it["index"]: it for it in items}
        _assert(len(items) == 3, "3 items returned")
        _assert(by_idx[0]["status"] == "completed", "item[0] completed")
        _assert(by_idx[1]["status"] == "completed", "item[1] completed")
        _assert(by_idx[2]["status"] == "failed", "item[2] failed")
        _assert(
            by_idx[2].get("error_code") == "bureau_failed",
            f"item[2] error_code=bureau_failed (got {by_idx[2].get('error_code')!r})",
        )
        _assert(
            final["items"]["completed"] == 2 and final["items"]["failed"] == 1,
            f"final counts completed=2 failed=1 (got {final['items']})",
        )
        print("\n🎉 Batch scoring flow verified end-to-end.")


if __name__ == "__main__":
    asyncio.run(main())
