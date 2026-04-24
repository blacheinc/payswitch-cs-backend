# Data Flows

Sequence diagrams for the three request paths that carry most of the system's semantic weight. Mermaid sources live alongside this file; GitHub renders them inline.

---

## 1. Single scoring request

![Scoring flow](./diagrams/scoring-flow.mmd)

**Entry**: `POST /v1/score-requests` with an applicant payload and optional `callback_url`.

**Key properties**:

- **Synchronous bureau lookup, asynchronous ML scoring.** The HTTP response returns as soon as the DE function accepts the payload — the client polls `GET /v1/score-requests/{tracking_id}/scoring-result` or waits for the webhook.
- **Hard stops are deterministic.** Seven rules in [`app/services/hard_stops.py`](../../app/services/hard_stops.py) (underage, over-age, KYC fail, judgement, write-off, delinquency, 3+ accounts in arrears). Triggering any rule produces an immediate DECLINE without hitting the DE function.
- **Tracking ID (`SCR-<12hex>`) is the only cross-system identifier.** It appears in ScoreRequest, ScoringResult, Service Bus payloads, and webhook deliveries. Pass it back to the client so they can correlate.
- **Service Bus is the decoupling seam.** DE publishes `scoring.complete` when it's done; the backend subscriber persists the result. If the subscriber is behind (replica still spinning up), messages queue — they're not dropped. Falls back to in-memory when no Service Bus connection string is configured (dev only).

---

## 2. Batch scoring

![Batch flow](./diagrams/batch-flow.mmd)

**Entry**: `POST /v1/score/batch` with up to 100 applicant payloads.

**Key properties**:

- **Submission is fire-and-forget.** API returns a `job_id` after inserting the BulkScoringJob + N BatchJobItem rows and pushing item IDs onto the Redis queue. No external calls happen on the request path.
- **Workers are in-process.** 5 asyncio tasks per Container App replica, started by the `lifespan` hook in `app/main.py`. Each worker is a plain `BRPOP` loop in `app/workers/batch_worker.py`. Horizontal scaling: replicas × 5 = total parallelism.
- **Per-item isolation.** A failing item doesn't stop the job; it's marked `FAILED` with `error_code = bureau_failed | scoring_error` and the next worker pulls the next item.
- **Status is derived from items.** `GET /v1/score/batch/{job_id}` aggregates BatchJobItem statuses with a SQL `GROUP BY`. The job itself only tracks coarse counters (`total_records`, `processed_records`, `error_count`) so per-item state stays authoritative.
- **Cancellation is cooperative.** `POST /v1/score/batch/{job_id}/cancel` flips PENDING items to CANCELLED; items already being processed finish naturally (worker re-reads status before starting, skips non-PENDING).

See the FE guide ([`fe_batch_scoring_integration_guide.md`](../fe_batch_scoring_integration_guide.md)) for request/response shapes.

---

## 3. Authenticated request (RBAC resolution)

![RBAC flow](./diagrams/rbac-flow.mmd)

**Applies to**: every endpoint guarded by `require_permission("<code>")` or `require_any_permission("a", "b", ...)` in `app/dependencies.py`.

**Key properties**:

- **Three principal types — one resolver.** Admin JWTs, org-user JWTs, and API keys are all resolved into a shared `AuthContext` dataclass by `get_auth_context()`. Every downstream dependency reads from that object, so the permission check is identical regardless of auth method.
- **Rate limiting runs first.** `require_permission` chains on top of `check_rate_limit`, which chains on `get_auth_context`. This guarantees a bad actor hitting a 403-returning endpoint still burns their quota.
- **Two shortcuts avoid a `RolePermission` join in the hot path**:
  - Admin with SUPER_ADMIN → `{"*"}`
  - Org user with the ADMIN system role → the frozen set `ALL_ORG_PERMISSION_CODES`

  Custom roles (and the four operational platform roles) fall through to the full join and return an explicit code set.
- **Permissions are per-request-scoped.** `AuthContext.permissions` is lazy-loaded on first check and cached on the object — a request that invokes `require_permission` three times still does exactly one RBAC lookup.

See the RBAC integration guides ([`fe_rbac_integration_guide.md`](../fe_rbac_integration_guide.md) for org side, [`fe_admin_rbac_integration_guide.md`](../fe_admin_rbac_integration_guide.md) for platform side) for the FE-facing permission catalog.

---

## Other flows (pointer)

The following aren't sequence-diagrammed because they're short enough to describe inline:

| Flow | Description |
|---|---|
| **Login** | `POST /auth/login` → lookup admin or org user → verify bcrypt hash → (TOTP challenge if enabled) → issue access + refresh JWT. [`app/routers/auth.py`](../../app/routers/auth.py) |
| **API key auth** | Bearer value ≠ JWT → SHA-256 hash → `ApiKey.key_hash` lookup → org status check → `AuthContext(user_type="api_key")`. [`app/dependencies.py:230`](../../app/dependencies.py) |
| **Webhook delivery** | After `ScoringResult` insert, the subscriber enqueues a `WebhookDelivery` row and POSTs to every matching `WebhookConfig.url` with retry + dead-letter. Delivery status tracked per attempt. [`app/subscribers/scoring_complete.py`](../../app/subscribers/scoring_complete.py) |
| **Training pipeline events** | `model-training-started` / `model-training-completed` topics consumed by the backend to insert / update `ModelVersion` rows. Owned by the ML team; the backend is read-only on them. |
