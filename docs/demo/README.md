# Business Demo Dataset

The demo-data package populates every monitoring dashboard with a coherent,
realistic 45-day dataset. It is designed for demonstrations, frontend
integration and acceptance testing without invoking live bureau or AI services.

## Included coverage

[`scripts/seed_demo_data.py`](../../scripts/seed_demo_data.py) creates:

- Platform and organisation identities with working RBAC assignments.
- Five representative financial institutions across supported lifecycle states.
- Score requests, decisions, grades and loan-performance outcomes.
- Batch jobs and item-level results.
- Risk, ModelOps, Compliance and Infrastructure dashboard metrics.
- Model metadata, data sources, alerts and audit activity.

The seed is deterministic enough for repeatable UI validation while preserving
realistic distributions and timestamps. Schema ownership remains with Alembic;
the seeder only creates demonstration records.

## Local run

```bash
# Start local dependencies
docker compose up -d postgres redis

# Build the complete current schema
.venv/bin/alembic upgrade head

# Populate the dataset
.venv/bin/python scripts/seed_demo_data.py --reset --scale medium

# Start the API
DEBUG=false .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Available scales are `small`, `medium` and `large`. The `--reset` option
rebuilds only the declared demo tables and reseeds all required RBAC links.

## Demo identities

| Persona | Email | Password |
|---|---|---|
| Platform administrator | `admin@payswitch.demo` | `DemoPass!2026` |
| Organisation administrator | `admin@<short-name>.com.gh` | `DemoPass!2026` |

Organisation short names are `gcm`, `accradigital`, `kumasifin`, `voltasacco`
and `tamalelend`.

## Dashboard coverage

The dataset supports the ModelOps, Risk, Infrastructure and Compliance
dashboards, alert feeds, platform score-request statistics, organisation lists,
batch scoring views and organisation score-request views.

The ModelOps champion view uses persisted model-version metadata when a remote
champion artifact is not configured, which keeps local and isolated acceptance
environments fully representative.

## Deploying a dedicated demo environment

Use a non-production environment with its own PostgreSQL and Redis instances.
Apply migrations before seeding, provide a strong environment-specific JWT
secret, and omit optional Azure integration connection strings when those
services are intentionally excluded from the demo.

```bash
DATABASE_URL="postgresql+asyncpg://<user>:<password>@<host>:<port>/<database>" \
APP_ENV=staging \
JWT_SECRET_KEY="<strong-demo-secret>" \
.venv/bin/python scripts/seed_demo_data.py --reset --scale medium
```

For Railway, use the PostgreSQL service's private URL from the backend and its
public proxy only for a one-time seed from an authorized workstation. For
Kubernetes, the same command can run as a controlled one-off Job after the Argo
PreSync migration has completed.

Demo credentials and seeded personal data are synthetic and must remain limited
to non-production environments.
