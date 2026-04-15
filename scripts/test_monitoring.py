"""
PaySwitch Monitoring & Dashboard APIs — Smoke Test.

Hits every endpoint with a valid request and prints the key values
from each response so you can confirm the data is real and correct.

Run:
    python scripts/test_monitoring.py
    docker exec csbackend-api-1 python scripts/test_monitoring.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx

sys.path.append(os.getcwd())

BASE_URL = "http://localhost:8000"
ADMIN_EMAIL = "engineering@opensaucery.africa"
ADMIN_PASS = "opensaucery"

G = "\033[92m"
R = "\033[91m"
B = "\033[94m"
Y = "\033[93m"
X = "\033[0m"


def section(title: str):
    print(f"\n{B}── {title} {'─' * max(1, 55 - len(title))}{X}")


def ok(label: str, value: str = ""):
    print(f"  {G}✔{X}  {label}" + (f"\n       {Y}{value}{X}" if value else ""))


def fail(label: str, detail: str = ""):
    print(f"  {R}✘{X}  {label}" + (f"\n       {R}{detail}{X}" if detail else ""))
    sys.exit(1)


def show(label: str, value):
    """Print a labelled value without asserting pass/fail."""
    if isinstance(value, (dict, list)):
        formatted = json.dumps(value, indent=6)
        print(f"  {Y}→{X}  {label}:\n       {Y}{formatted}{X}")
    else:
        print(f"  {Y}→{X}  {label}: {Y}{value}{X}")


async def main():
    print(f"\n{B}{'═' * 60}{X}")
    print(f"{B}  PaySwitch Monitoring — Smoke Test{X}")
    print(f"{B}{'═' * 60}{X}")

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as c:

        # ── Login ────────────────────────────────────────────────────
        section("Admin Login")
        r = await c.post("/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASS})
        if r.status_code != 200 or "access_token" not in r.json():
            fail("Login failed", f"status={r.status_code}  body={r.text[:300]}")
        token = r.json()["access_token"]
        ok("Logged in", f"user_type={r.json().get('user_type')}")
        h = {"Authorization": f"Bearer {token}"}

        # ── GET /v1/models/current ────────────────────────────────────
        section("GET /v1/models/current")
        r = await c.get("/v1/models/current", headers=h)
        if r.status_code == 200:
            d = r.json()
            ok("Champion metadata returned")
            show("updated_at", d.get("updated_at"))
            for m in d.get("models", []):
                show(f"model: {m['model_type']}", {
                    "version": m.get("version"),
                    "status": m.get("status"),
                    "metrics": m.get("metrics"),
                })
        elif r.status_code == 404:
            ok("404 — no training run completed yet (expected on fresh env)", r.json().get("detail", ""))
        else:
            fail("Unexpected response", f"status={r.status_code}  body={r.text[:300]}")

        # ── POST /v1/rules/evaluate ───────────────────────────────────
        section("POST /v1/rules/evaluate")
        body = {
            "probability_of_default": 0.18,
            "score_grade": "D",
            "data_engineer_decision_label": "CONDITIONAL_APPROVE",
            "fraud_risk_flag": "LOW",
            "recommended_loan_amount_ghs": 30000,
            "features": {"num_active_accounts": 0.65, "utilisation_ratio": 0.78},
            "metadata": {"credit_score": 615, "applicant_age_at_application": 34},
        }
        r = await c.post("/v1/rules/evaluate", json=body, headers=h)
        if r.status_code == 200:
            d = r.json()
            ok("Rule evaluation returned 200")
            show("request_id", d.get("request_id"))
            show("sandbox", d.get("sandbox"))
            show("decision", d.get("decision"))
            show("condition_applied", d.get("condition_applied"))
            show("credit_risk", d.get("credit_risk"))
            show("fraud_detection", d.get("fraud_detection"))
            show("loan_amount", d.get("loan_amount"))
        elif r.status_code == 502:
            ok("502 — orchestrator unreachable (expected if not deployed)", r.json().get("detail", ""))
        else:
            fail("Unexpected response", f"status={r.status_code}  body={r.text[:300]}")

        # ── GET /v1/monitoring/infrastructure ─────────────────────────
        section("GET /v1/monitoring/infrastructure")
        r = await c.get("/v1/monitoring/infrastructure?period=24h", headers=h)
        if r.status_code != 200:
            fail("Request failed", f"status={r.status_code}  body={r.text[:300]}")
        d = r.json()
        ok("Infrastructure dashboard returned 200")
        show("period", d.get("period"))
        show("generated_at", d.get("generated_at"))
        show("request_volume.total", d["request_volume"]["total"])
        show("request_volume.by_endpoint", d["request_volume"]["by_endpoint"][:3])
        show("latency", {
            "p50_ms": d["latency"]["p50_ms"],
            "p95_ms": d["latency"]["p95_ms"],
            "p99_ms": d["latency"]["p99_ms"],
        })
        show("error_rates.overall_pct", d["error_rates"]["overall_pct"])
        show("timeseries buckets", len(d.get("timeseries", [])))
        show("alerts", d.get("alerts"))

        # ── GET /v1/monitoring/risk ───────────────────────────────────
        section("GET /v1/monitoring/risk")
        r = await c.get("/v1/monitoring/risk?period=7d", headers=h)
        if r.status_code != 200:
            fail("Request failed", f"status={r.status_code}  body={r.text[:300]}")
        d = r.json()
        ok("Risk dashboard returned 200")
        show("period", d.get("period"))
        oa = d["approval_rates"]["overall"]
        show("overall approval rates", {
            "total_decisions": oa["total_decisions"],
            "approve_rate_pct": oa["approve_rate_pct"],
            "decline_rate_pct": oa["decline_rate_pct"],
            "conditional_approve_rate_pct": oa["conditional_approve_rate_pct"],
        })
        show("approval_rates.by_grade", d["approval_rates"]["by_grade"])
        show("score_distribution", {
            "mean": d["score_distribution"]["mean"],
            "median": d["score_distribution"]["median"],
            "std_dev": d["score_distribution"]["std_dev"],
        })
        show("risk_tier_breakdown", d.get("risk_tier_breakdown"))
        show("timeseries buckets", len(d.get("timeseries", [])))
        show("alerts", d.get("alerts"))

        # ── GET /v1/monitoring/model-ops ──────────────────────────────
        section("GET /v1/monitoring/model-ops")
        r = await c.get("/v1/monitoring/model-ops?period=30d", headers=h)
        if r.status_code != 200:
            fail("Request failed", f"status={r.status_code}  body={r.text[:300]}")
        d = r.json()
        ok("Model-ops dashboard returned 200")
        show("period", d.get("period"))
        show("champions", [
            {
                "model_type": m.get("model_type"),
                "version": m.get("version"),
                "status": m.get("status"),
                "live_auc": m.get("live_auc"),
                "auc_change_pct": m.get("auc_change_pct"),
            }
            for m in d.get("champions", [])
        ])
        show("feature_drift", d.get("feature_drift"))
        show("retraining_history", d.get("retraining_history", [])[:3])
        show("score_distribution_psi", d.get("score_distribution_psi"))
        show("alerts", d.get("alerts"))

        # ── GET /v1/monitoring/compliance ─────────────────────────────
        section("GET /v1/monitoring/compliance")
        r = await c.get("/v1/monitoring/compliance?period=30d", headers=h)
        if r.status_code != 200:
            fail("Request failed", f"status={r.status_code}  body={r.text[:300]}")
        d = r.json()
        ok("Compliance dashboard returned 200")
        show("period", d.get("period"))
        show("fairness — approval_rate_by_age_group",
             d["fairness_metrics"]["demographic_parity"]["approval_rate_by_age_group"])
        show("fairness — max_disparity_pct",
             d["fairness_metrics"]["demographic_parity"]["max_disparity_pct"])
        show("audit_log", d.get("audit_log"))
        show("data_subject_requests", d.get("data_subject_requests"))
        show("alerts", d.get("alerts"))

        # ── GET /v1/monitoring/alerts ─────────────────────────────────
        section("GET /v1/monitoring/alerts")
        r = await c.get("/v1/monitoring/alerts", headers=h)
        if r.status_code != 200:
            fail("Request failed", f"status={r.status_code}  body={r.text[:300]}")
        d = r.json()
        ok("Alerts feed returned 200")
        show("generated_at", d.get("generated_at"))
        show("summary", d.get("summary"))
        show("alerts (first 5)", d.get("alerts", [])[:5])

    print(f"\n{G}{'═' * 60}{X}")
    print(f"{G}  All endpoints responded as expected.{X}")
    print(f"{G}{'═' * 60}{X}\n")


if __name__ == "__main__":
    asyncio.run(main())
