# Data Model

One PostgreSQL database, ~30 tables grouped into five logical domains. Every table uses UUID primary keys, UTC `created_at` / `updated_at` timestamps (via the `TimestampMixin`), and explicit foreign keys with named relationships.

![ER diagram](./diagrams/er-diagram.mmd)

The diagram is condensed — only the columns relevant to the graph and the most operationally important attributes are shown. Full definitions live in [`app/models/`](../../app/models/). Schema migrations under [`alembic/versions/`](../../alembic/versions/) are the ultimate source of truth.

## Domain grouping

### Identity & RBAC (9 tables)

The foundation for every other domain. Three principal types (`AdminUser`, `OrgUser`, `ApiKey`) share a single role/permission graph. System roles (`SUPER_ADMIN`, the four operational platform roles, the org-side `ADMIN`) are `is_system=true` and cannot be edited through the API — their permission sets are synced by the seeder at each boot. See the [admin RBAC guide](../fe_admin_rbac_integration_guide.md) for the authoritative role matrix.

### Scoring (4 tables)

`ScoreRequest` is the durable record of every scoring attempt (single-request flow). `ScoringResult` holds the ML outputs as JSONB and joins back by `tracking_id`. `ScoreDecision` captures the operator's final approve/decline decision and amounts. `LoanPerformance` is how the client reports back post-disbursement delinquency data for feedback-loop training.

### Batch Scoring (2 tables)

`BulkScoringJob` tracks a batch submission; `BatchJobItem` is one row per applicant inside that batch. Items link to their produced `ScoreRequest` when scoring reaches the pipeline — failed items store an `error_code` + `error_message` instead.

### Data Sources & Training (5 tables)

`DataSource` → one per training partner (bank / bureau / telco). `TrainingUpload` → one per ingested file, with a JSONB `models_to_train` list and lifecycle states. `FieldMapping` → the LLM- or template-derived source-column → feature mapping, reviewable by the admin. `MappingTemplate` → version-controlled frozen mappings reused across uploads. `ModelVersion` → the registry of trained models (`model_type` × `version`) with MLflow run ID and Azure ML artifact path.

### Observability (7 tables)

- `ApiLog` — every HTTP request, scoped per org (nullable for admin/platform calls).
- `RequestMetric` — infrastructure-level latency / status-class counters feeding the Infrastructure Dashboard.
- `AuditLog` — actor-centric append-only log for compliance (actor type + action + changes JSONB).
- `WebhookConfig` / `WebhookDelivery` — fan-out + per-attempt status for outbound event delivery.
- `Alert` — the unified alert feed across the 4 dashboards.
- `ModelMetric`, `DecisionMetric` — longitudinal aggregates powering the Risk and ModelOps dashboards.

## Design principles

- **No cross-domain cascades.** Foreign keys use `ON DELETE SET NULL` or `ON DELETE CASCADE` only within a domain. Deleting an organization does not cascade into historical `ScoringResult` — audit history is preserved.
- **JSONB for unbounded shape.** Scoring results, model metrics, field mappings, and webhook payloads use JSONB to avoid migrating every time the data team adds a feature. Indexed fields (decision enum, status) stay relational.
- **Enum columns with `values_callable`.** Postgres native enums are used everywhere (`AdminRole`, `OrgUserStatus`, `BatchJobItemStatus`, etc.). New values require an Alembic migration; the Python enum and the Postgres enum are kept lock-step by the pattern in `app/db/base.py`.
- **Soft deletes, not hard deletes.** `OrgUser` has a `REMOVED` status; `AdminUser` uses `DEACTIVATED`; `ApiKey` has `REVOKED`. Nothing is physically deleted from the authoritative tables so audit links stay intact.
- **UUID pks everywhere.** Lets the client mint IDs client-side for idempotency if they ever want to, and keeps ER joins boring.

## Migration policy

- **Every schema change ships as an Alembic revision.** Never edit the DB directly.
- **Migrations are release-gated in production** (see `entrypoint.sh`). Traffic is admitted only after the target schema revision has applied successfully.
- **Backfills that exceed ~60 s runtime** should be split: the migration adds the column with a null-safe default, a follow-up script does the backfill, and a later migration adds the `NOT NULL` constraint. Keeps container boot under the ACA health-probe grace window.

## Inspecting the schema on a live deploy

```bash
# Connect (from a bastion / dev machine with Postgres firewall access):
psql "host=<postgresFqdn> dbname=credit_scoring user=<admin> sslmode=require"

# List tables:
\dt

# Describe a specific table:
\d score_requests
```

In CI, use `alembic check` to confirm the declared ORM state matches what the migrations produce — this is wired into the test matrix.
