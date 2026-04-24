# Operational Runbook

Day-2 operations for the running deployment. Scope is operator-facing — assumes familiarity with `az`, `kubectl`-style command patterns, and Log Analytics.

---

## Where to find things

| Thing | Location |
|---|---|
| Container App | Azure portal → Resource group → `<prefix>-<env>-api` |
| Live logs | Log Analytics workspace → Logs → `ContainerAppConsoleLogs_CL` |
| DB schema migrations | `alembic/versions/` in the repo |
| App configuration | Key Vault (`<prefix><env>kv…`) + Container App env vars |
| Deployment history | `az deployment sub list --query "[?contains(name, 'payswitch')]"` |
| Scoring decisions | Postgres: `scoring_results` + `score_decisions` tables |
| Audit trail | Postgres: `audit_logs` table |

---

## Log Analytics queries

```kql
// Last 50 app log lines
ContainerAppConsoleLogs_CL
| where ContainerAppName_s == '<acaAppName>'
| order by TimeGenerated desc
| take 50
| project TimeGenerated, RevisionName_s, Log_s

// 5xx spikes in the last hour
ContainerAppConsoleLogs_CL
| where ContainerAppName_s == '<acaAppName>'
| where Log_s contains " 5"
| summarize count() by bin(TimeGenerated, 1m)

// Slow endpoints (p95 > 1 s) from ApiLog-style structured entries
ContainerAppConsoleLogs_CL
| where Log_s has "response_time_ms"
| extend rt = toint(extract(@"response_time_ms=(\d+)", 1, Log_s))
| summarize p95=percentile(rt, 95) by endpoint=extract(@"path=([^\s]+)", 1, Log_s)
| where p95 > 1000
| order by p95 desc
```

---

## Scaling

**Horizontal (replicas).** HTTP-concurrency autoscale is configured in `aca-app.bicep`; defaults: min 1, max 5, 50 concurrent req/replica before scale-out. Change:

```bash
az containerapp update --name <acaApp> --resource-group <rg> \
  --min-replicas 2 --max-replicas 10 \
  --scale-rule-name http-concurrency \
  --scale-rule-http-concurrency 75
```

Each replica hosts 5 batch workers + 3 Service Bus subscribers. Raising replicas is the preferred way to increase batch throughput.

**Vertical (CPU/memory).** Edit `aca-app.bicep` params `cpu` and `memory` and re-run `./scripts/deploy.sh --skip-build`. Valid combinations: `0.25/0.5Gi`, `0.5/1Gi`, `0.75/1.5Gi`, `1/2Gi`, `1.25/2.5Gi`, `1.5/3Gi`, `1.75/3.5Gi`, `2/4Gi`.

**Postgres SKU.** Via portal or:

```bash
az postgres flexible-server update --resource-group <rg> --name <pgServer> \
  --sku-name Standard_D2s_v3 --tier GeneralPurpose
```

Burstable B2ms is fine for < 50 req/s sustained; move to General Purpose at higher load.

**Redis.** C0 Basic is 250 MB. Bump via `az redis update --sku Standard --vm-size C2` (or edit Bicep). Standard gives replication for HA.

---

## DB migrations on a live deploy

Migrations run automatically on every boot (`alembic upgrade head` in `entrypoint.sh`). For new schema changes:

```bash
# 1. Develop locally, create the revision
alembic revision --autogenerate -m "describe change"

# 2. Inspect alembic/versions/<hash>_describe_change.py

# 3. Commit + deploy
./scripts/deploy.sh --skip-infra   # triggers a new image → new revision → migration runs
```

**Long-running migrations** (backfills > 60 s) should NOT run in the entrypoint — they'll trip ACA's health-probe grace window. Pattern:

1. Migration adds the column with a null-safe default (fast).
2. A separate script (run via `az containerapp exec` or a one-off Jobs container) does the backfill.
3. A follow-up migration adds `NOT NULL`.

---

## Secret rotation

**Database password:**

```bash
# 1. Reset the Postgres admin password
az postgres flexible-server update --resource-group <rg> --name <pgServer> \
  --admin-password '<new-password>'

# 2. Update the KV secret
az keyvault secret set --vault-name <kvName> \
  --name database-url \
  --value 'postgresql+asyncpg://<admin>:<new-password>@<pgFqdn>:5432/credit_scoring?ssl=require'

# 3. Restart the app to pick up the new secret
az containerapp revision restart --name <acaApp> --resource-group <rg>
```

**JWT secret key:** same pattern but expect all active sessions to invalidate.

**SP for Key Vault:** see [`post-deploy.md`](./post-deploy.md#8-rotate-the-service-principal).

---

## Incident response

### Scenario: Container App is Unhealthy

```bash
# Current revisions
az containerapp revision list --name <acaApp> --resource-group <rg> \
  --query "[].{name:name, active:properties.active, health:properties.healthState, image:properties.template.containers[0].image}" -o table

# Roll back to the previous healthy revision
az containerapp revision activate --revision <previous-name> --name <acaApp> --resource-group <rg>
az containerapp revision deactivate --revision <bad-name> --name <acaApp> --resource-group <rg>
```

### Scenario: Postgres down

1. `az postgres flexible-server show --name <pgServer> --resource-group <rg>` — check `state`.
2. If the DB is past the backup window, trigger point-in-time restore (see [`post-deploy.md §7`](./post-deploy.md#7-back-up-verification)).
3. If the app is still up, pending writes accumulate in the event loop; it will 500 within a few seconds. Notify clients; scale replicas to 0 to stop drain.

### Scenario: Redis down

App keeps running but rate limiting is disabled and batch workers go idle. This is intentional by design (see `lifespan`'s Redis-optional branch). Restore Redis and workers pick up pending items from the persisted queue.

### Scenario: Service Bus down

Subscribers can't deliver `scoring.complete` / training events to the backend. New score requests return 202 but never transition to COMPLETED. Once Service Bus returns, subscribers reconnect and drain; no data loss (DE publishes persistently).

### Scenario: All replicas scaled to 0 or stuck provisioning

```bash
# Force a revision rollout — often clears transient scheduler issues
az containerapp update --name <acaApp> --resource-group <rg> \
  --set-env-vars APP_ENV=production
```

---

## Disaster recovery

**RPO / RTO targets (default Azure SKUs):**

| Concern | Default | Notes |
|---|---|---|
| Postgres | RPO < 15 min, RTO < 1 h | Backup retention 7 d; PITR available |
| Redis | RPO not guaranteed | Basic C0 has no replication — upgrade to Standard for 99.9% |
| Service Bus | RPO 0 | Standard SKU replicates; Basic doesn't |
| Storage | RPO < 15 min (LRS) | Upgrade to ZRS for zone-redundant |
| Key Vault | Soft-delete 7 d | Purge protection disabled by default (Bicep param) |

Full-region DR is not included in v1. To enable: upgrade SKUs (Postgres GP + geo-redundant backup, Redis Premium + geo-replication, Service Bus Premium + pairing, Storage GRS) and add a paired-region Bicep variant.

---

## Upgrading the application

1. Pull the new code + parameters from the delivery team.
2. Review `CHANGELOG.md` for breaking changes (env var additions, new perms, migration notes).
3. `./scripts/deploy.sh --skip-infra` deploys code-only changes.
4. `./scripts/deploy.sh` re-runs the full pipeline (safe — idempotent).
5. Smoke-test `/health`, a scoring request, and an admin login.

If a deploy needs to be rolled back, use `az containerapp revision activate` (see Incident Response above) rather than `git revert` — faster and non-disruptive.
