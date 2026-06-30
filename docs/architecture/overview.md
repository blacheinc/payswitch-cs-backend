# Architecture Overview

> Target audience: engineers onboarding onto the backend or operators tuning the production deploy.
> Scope: the PaySwitch Credit Scoring API, its direct dependencies and its integration boundaries with the data-engineering and ML services.

![Architecture](./diagrams/architecture.mmd)

## Topology at a glance

One public-facing Azure Container App hosts everything in a single FastAPI process. No separate worker tier — the batch scoring workers and Service Bus subscribers are async tasks launched from the FastAPI `lifespan` hook. Horizontal scaling via ACA HTTP-concurrency autoscaling (1–5 replicas by default) multiplies the worker pool linearly.

The container authenticates to every Azure data-plane service with a **connection string or access key** (Postgres password, Redis access key, Service Bus connection string, Storage key). Key Vault authentication uses a pre-provisioned service principal (`client-id`, `client-secret` and `tenant-id`) — see the [secret-source module](../../app/config/keyvault_source.py).

## Component responsibilities

| Component | Role | Availability and continuity design |
|---|---|---|
| **FastAPI app** (ACA) | HTTP ingress + in-process batch workers + Service Bus subscribers | Readiness probes admit traffic only after required startup checks pass. |
| **PostgreSQL** | System of record — users, roles, orgs, scoring results, batch jobs, audit logs, ML metadata | Required dependency; deployment sequencing and readiness checks protect rollout integrity. |
| **Redis** | Rate limiter, idempotency cache, batch-worker job queue | Readiness reports connectivity; operations can restore service without data loss from the system of record. |
| **Service Bus** | Pub/sub for ML events (`training.*`) and scoring completions | In-memory development fallback is available; production uses durable cross-replica messaging. |
| **Blob Storage** | Bulk uploads, training data, reports, champion model artifacts | Clients initialize on demand and expose clear integration errors when unavailable. |
| **Key Vault** | Runtime secret store for fields listed in [`SECRET_FIELDS`](../../app/config/keyvault_source.py) | Optional secret source with controlled environment-variable fallback. |
| **ACR** | Holds the API image | Deployment dependency; replicas use immutable image references. |
| **Log Analytics** | Container stdout/stderr + structured app logs | Centralized diagnostics complement application audit and metric tables. |
| **XDS Bureau** | External credit-bureau lookups (Ghana) | Controlled `bureau_failed` outcomes preserve traceability and per-item batch isolation. |
| **DE Inference Function** | ML scoring pipeline (credit risk / fraud / loan amount / income verification) | Controlled scoring outcomes preserve the request audit trail. |
| **Orchestrator Function** | Decision-rule evaluation (post-ML) | Uses the same controlled scoring and traceability contract. |
| **Resend / SMTP** | Transactional email (invites, password reset, provisioning) | Delivery is independently logged while the underlying invitation record remains valid. |

## Runtime footprint per replica

| Resource | Default | Notes |
|---|---|---|
| CPU | 0.5 cores | `aca-app.bicep` `cpu` param |
| Memory | 1.0 GiB | `aca-app.bicep` `memory` param |
| Batch workers | 5 async tasks | `WORKER_COUNT` in `app/workers/batch_worker.py` |
| SB subscribers | 3 | one per consumed topic |

**Replica count affects worker throughput.** At 3 replicas the service provides 15 batch workers in aggregate. Horizontal scaling is the preferred way to increase throughput while retaining predictable per-process resource use.

## Security posture

See [`security.md`](./security.md) for the full write-up. Headline points:

- JWT (HS256) for dashboard / mobile; bcrypt for password hashes; TOTP for admin 2FA.
- RBAC with explicit permission codes — every endpoint declares what it needs via `require_permission(...)`.
- API keys hashed (`sha256`) at rest; only the `sk_{test,live}_…` prefix + last-used timestamp are inspectable.
- Secrets via Key Vault with env fallback (service-principal client-id/secret credential).
- Postgres TLS-required; Redis TLS-only (`rediss://`); Service Bus SAS (connection string); Storage HTTPS-only.
- CORS origins come from `ALLOWED_ORIGINS` env var — empty means "reject all".
- Rate limiting is Redis-backed (`RateLimiter` in `app/core/rate_limiter.py`) keyed by organization.

## Data flows

Documented with sequence diagrams in [`data-flows.md`](./data-flows.md):

- **Scoring** — single request → bureau → hard stops → DE → scoring result → webhook delivery.
- **Batch scoring** — job submission → Redis queue → N workers → per-item bureau + DE → aggregated results.
- **RBAC** — login → JWT issue → request → permission resolver → 200/403.

## Data model

Documented in [`data-model.md`](./data-model.md) with an ER diagram. Highlights:

- ~30 tables grouped into: Identity & RBAC, Scoring, Batch Scoring, Data Sources & Training, Observability.
- No cross-concern foreign keys — e.g. `ScoringResult` links to `ScoreRequest` *or* to `BulkScoringJob` but never both.
- Audit / telemetry tables (`ApiLog`, `RequestMetric`, `AuditLog`) are intentionally FK-light so they can be retention-managed without breaking the core graph.

## Deployment model

Client-owned Azure environment. The deploy artifacts live under [`infra/bicep/`](../../infra/bicep/) and [`scripts/deploy.sh`](../../scripts/deploy.sh); see [`docs/deployment/README.md`](../deployment/README.md) for the operator playbook.
