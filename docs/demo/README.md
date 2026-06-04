# Business-demo data (no Azure / no live AI)

This package lets the platform render rich, realistic data on every dashboard
**without** Azure, without the AI scoring pipeline, and without running any live
scoring. It is purely a data-population layer for a business/sales demo.

What was done:

- **Local env** repointed at Docker Postgres; Azure Service Bus / Blob / Champion
  storage connection strings blanked in `.env` (the app falls back to an
  in-memory queue and `None` blob reads — it boots clean). Original Azure values
  are preserved in `.env.azure.bak`.
- **`scripts/seed_demo_data.py`** populates ~20 tables through the ORM with a
  coherent ~45-day history covering **every enum value / decision outcome** at
  least once (all decisions incl. ERROR/FRAUD_HOLD, grades A–F, all request /
  model-lifecycle / batch / loan-performance statuses, alerts across all four
  dashboards, metrics, model metadata).
- **`app/routers/monitoring/model_ops.py`** patched so the model-ops "champions"
  list falls back to the `model_versions` table when the champion blob is absent
  (Azure storage is gone). This is the correct behaviour for a no-Azure deploy.

Redis is already on Railway and is reused as-is.

---

## Run locally

```bash
# 1. Start Postgres (Redis is remote on Railway; no local Redis needed)
docker compose up -d postgres

# 2. Apply schema
.venv/bin/alembic upgrade head

# 3. Seed demo data (idempotent enum-fix runs automatically)
.venv/bin/python scripts/seed_demo_data.py --reset --scale medium
#   scales: small | medium | large

# 4. Start the API
DEBUG=false .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
#   docs: http://localhost:8000/docs
```

### Demo logins

| Who          | Email                       | Password        |
|--------------|-----------------------------|-----------------|
| Platform admin (super) | `admin@payswitch.demo`         | `DemoPass!2026` |
| Org admin (per org)    | `admin@<short>.com.gh`         | `DemoPass!2026` |

Org short-names: `gcm`, `accradigital`, `kumasifin` (active), `voltasacco`
(suspended), `tamalelend` (pending). e.g. `admin@gcm.com.gh`.

### Verified dashboards (all 200 with rich data)

`/v1/monitoring/model-ops` · `/risk` · `/infrastructure` · `/compliance` ·
`/alerts` · `/admin/score-requests/stats` · `/admin/organizations` ·
`/v1/score/batch` · org `/v1/score-requests/stats` · `/v1/score-requests`.

---

## Why the enum-normalization step exists

A **fresh** `alembic upgrade head` creates four enum types out of sync with the
ORM models — `industry_type`, `source_type`, `training_data_status` are created
with UPPERCASE labels, and `decision_type` only has the legacy
`APPROVED/DECLINED/REFERRED/PENDING` set. The models bind the lowercase `.value`
(and the newer decision values), so the app itself cannot read/write those tables
on a freshly-migrated DB. `seed_demo_data.py` runs an idempotent
`normalize_enums()` step that renames/adds the labels to match the models. This
is required on **any** DB built from these migrations (local and Railway).

> Follow-up worth doing later: fold `normalize_enums()` into a proper Alembic
> migration so the schema is correct without the seeder.

---

## Deploy + seed on Railway

The Railway project already runs the backend + Redis. Add Postgres and point the
backend at it.

### 1. Add a Postgres service
In the Railway project: **New → Database → PostgreSQL**. It exposes `PGHOST`,
`PGPORT`, `PGUSER`, `PGPASSWORD`, `PGDATABASE`, a private host
`postgres.railway.internal`, and a public TCP proxy (like the Redis
`*.proxy.rlwy.net` host).

### 2. Set the backend service variables
On the **backend** service (Variables tab):

```
# asyncpg driver + Railway PRIVATE networking (note the +asyncpg scheme).
# Replace `Postgres` with the actual Postgres service name if different:
DATABASE_URL = postgresql+asyncpg://${{Postgres.PGUSER}}:${{Postgres.PGPASSWORD}}@${{Postgres.RAILWAY_PRIVATE_DOMAIN}}:5432/${{Postgres.PGDATABASE}}

# IMPORTANT: use staging (NOT production). In production, validate_production()
# REQUIRES the Azure Service Bus + Storage connection strings to be non-empty,
# so blanking them under APP_ENV=production crashes the boot. staging skips that
# check and lets the app run on the in-memory queue + DB fallback.
APP_ENV = staging
REDIS_URL = (leave as-is — already pointing at Railway Redis)
JWT_SECRET_KEY = <set a real, non-"CHANGE-ME" secret>
BOOTSTRAP_ADMIN_EMAIL = admin@payswitch.demo
BOOTSTRAP_ADMIN_PASSWORD = DemoPass!2026
BOOTSTRAP_ADMIN_NAME = Platform Administrator

# Blank the Azure integrations so the app uses the in-memory queue + DB fallback:
AZURE_SERVICEBUS_CONNECTION_STRING =
AZURE_STORAGE_CONNECTION_STRING =
CHAMPION_STORAGE_CONNECTION_STRING =
```

> The `+asyncpg` scheme is required — Railway's stock `DATABASE_URL` is a plain
> `postgresql://` URL and the async engine needs `postgresql+asyncpg://`.
> A literal alternative (public proxy) also works:
> `postgresql+asyncpg://postgres:<pw>@<host>.proxy.rlwy.net:<port>/railway`.

### 3. Deploy the code
Push this branch (includes the `model_ops.py` champion fallback). On deploy, the
container entrypoint runs `alembic upgrade head` automatically, then bootstraps
the admin. Confirm the service is healthy at `/health`.

### 4. Seed Railway's Postgres
Easiest is to seed from your laptop against Railway's **public** Postgres proxy
(same idea as the Redis `*.proxy.rlwy.net` URL):

```bash
DATABASE_URL="postgresql+asyncpg://<PGUSER>:<PGPASSWORD>@<region>.proxy.rlwy.net:<PROXY_PORT>/<PGDATABASE>" \
DEBUG=false \
.venv/bin/python scripts/seed_demo_data.py --reset --scale medium
```

(Get the public host/port from the Postgres service → **Connect → Public Network**.)

The seeder runs the enum-normalization first, so a fresh Railway DB is handled
automatically. After it finishes, refresh the dashboards — they render the same
data you verified locally.

> If you seed **after** the backend is already serving traffic, restart the
> backend service once afterwards so startup re-seeds the RBAC roles that
> `--reset` rebuilds. (Locally we seed before starting the API, so this is a
> no-op there.)
