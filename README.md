# PaySwitch Credit Scoring Platform — Backend

B2B SaaS credit scoring API for Ghanaian financial institutions. FastAPI + PostgreSQL + Redis + Azure Service Bus + Azure Blob Storage + Azure Key Vault, packaged as a single container deployable to Azure Container Apps.

This repository is the **client-facing handoff package**. The delivery team has prepared the code, infrastructure-as-code, deploy scripts, and documentation. The client operator deploys this to their own Azure subscription.

---

## Quickstart (client operator)

```bash
# 1. Log in to Azure and select the target subscription
az login
az account set --subscription <sub-id>

# 2. Create the service principal used for Key Vault access
az ad sp create-for-rbac --name "payswitch-kv-prod" --years 2
az ad sp show --id <appId> --query id -o tsv    # SP objectId

# 3. Fill in every <PLACEHOLDER> in infra/bicep/main.parameters.json

# 4. Deploy
./scripts/deploy.sh
```

The script provisions every Azure resource, seeds Key Vault, builds and pushes the image to ACR, rolls out the Container App, and smoke-tests `/health`. Takes about 15 minutes.

See [`docs/deployment/README.md`](docs/deployment/README.md) for the full playbook.

---

## Documentation map

### Architecture
- **[`docs/architecture/overview.md`](docs/architecture/overview.md)** — component topology, responsibilities, startup dependencies.
- **[`docs/architecture/data-flows.md`](docs/architecture/data-flows.md)** — sequence diagrams for scoring, batch scoring, RBAC.
- **[`docs/architecture/data-model.md`](docs/architecture/data-model.md)** — ER diagram + schema domain grouping.
- **[`docs/architecture/security.md`](docs/architecture/security.md)** — authn, RBAC, secrets, transport, rate limiting.

### Deployment
- **[`docs/deployment/README.md`](docs/deployment/README.md)** — operator entrypoint.
- **[`docs/deployment/post-deploy.md`](docs/deployment/post-deploy.md)** — first-run checklist.
- **[`docs/deployment/runbook.md`](docs/deployment/runbook.md)** — day-2 operations (scaling, rotation, DR).
- **[`docs/deployment/troubleshooting.md`](docs/deployment/troubleshooting.md)** — known failure modes + fixes.
- **[`infra/bicep/README.md`](infra/bicep/README.md)** — Bicep parameter reference.

### Frontend integration
- [`docs/fe_rbac_integration_guide.md`](docs/fe_rbac_integration_guide.md) — org-side RBAC.
- [`docs/fe_admin_rbac_integration_guide.md`](docs/fe_admin_rbac_integration_guide.md) — platform-side RBAC.
- [`docs/fe_batch_scoring_integration_guide.md`](docs/fe_batch_scoring_integration_guide.md) — batch scoring API.
- [`docs/fe_monitoring_integration_guide.md`](docs/fe_monitoring_integration_guide.md) — admin monitoring dashboards.
- [`docs/fe_dashboard_integration_guide.md`](docs/fe_dashboard_integration_guide.md) — overview-dashboard endpoints (`/stats`, decision filters, model-ops `promoted_at`).
- [`docs/fe_admin_cross_org_reads_guide.md`](docs/fe_admin_cross_org_reads_guide.md) — admin cross-org reads + platform-wide score stats.
- [`docs/fe_changelog_2026-04-27.md`](docs/fe_changelog_2026-04-27.md) — delta-only release notes for the latest two FE-affecting changes (embedded `organization` block + `by_org` removal).
- [`docs/de_feature_mapping.md`](docs/de_feature_mapping.md) — data-engineering feature schema.

---

## Local development

```bash
# 1. Python 3.11 and a virtualenv
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .

# 2. Bring up Postgres + Redis via docker-compose
docker-compose up -d postgres redis

# 3. Run migrations + bootstrap admin
BOOTSTRAP_ADMIN_EMAIL=dev@local \
BOOTSTRAP_ADMIN_PASSWORD=dev-pass-123 \
  python scripts/create_superuser.py

# 4. Start the API
uvicorn app.main:app --reload
```

`http://localhost:8000/docs` → OpenAPI UI.

### Running tests

```bash
pytest tests/                           # unit suite (no external services)
python scripts/test_admin_admins_flow.py    # E2E CRUD against a running server
python scripts/test_batch_score_flow.py     # E2E batch scoring
```

---

## Repository layout

```
app/
  config/              # Settings + Key Vault secret source
  core/                # security, rate limiter, middleware, exceptions, events
  db/                  # async SQLAlchemy engine + session factory
  dependencies.py      # auth + permission FastAPI dependencies
  models/              # ORM tables (~30)
  routers/             # HTTP endpoints, grouped by concern
  schemas/             # Pydantic request/response models
  services/            # business logic (permissions, email, XDS, etc.)
  storage/             # Blob + Redis cache wrappers
  subscribers/         # Service Bus topic handlers
  workers/             # in-process async task runners (batch scoring)
  main.py              # FastAPI factory + lifespan hooks

alembic/
  versions/            # DB migrations (21+)

infra/
  bicep/               # Azure IaC (main + 10 modules + parameters template)

scripts/
  deploy.sh            # one-shot Azure deploy
  destroy.sh           # teardown
  create_superuser.py  # bootstrap admin
  test_*.py            # E2E test harnesses

tests/
  test_*.py            # pytest unit suite

docs/
  architecture/        # system docs (this guide's focus)
  deployment/          # operator playbook
  fe_*.md              # frontend integration guides
```

---

## Support

Questions about deployment, configuration, or operational incidents: contact the delivery team through the channel your account manager provided.

Bug reports or feature requests: open an issue in the repository.
