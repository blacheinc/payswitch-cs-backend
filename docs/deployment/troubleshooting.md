# Troubleshooting

Operator reference for common diagnostics. Pattern: **symptom → likely cause → recovery**.

---

## Deploy-time

### `./scripts/deploy.sh` exits with `Azure CLI (az) not found`
You don't have the Azure CLI on `PATH`. Install from <https://aka.ms/installazurecliwindows> or `brew install azure-cli`.

### `Not logged in` on preflight
Run `az login` and `az account set --subscription <sub-id>` before re-running the script.

### `Bicep validation failed`
Read `/tmp/payswitch-bicep-validate.log` (path printed by the script). Usual culprits:

- `<PLACEHOLDER>` still present in `main.parameters.json` — replace every angle-bracket value.
- `appPrincipalObjectId` set to the SP's **appId** instead of its **objectId**. Re-run `az ad sp show --id <appId> --query id -o tsv` and use that.
- `postgresAdminPassword` fails strength requirements — use 12+ chars with upper/lower/digit.
- Namespace / storage name collision — a previous deploy's resource is soft-deleted. See "Key Vault name already exists" below.

### Deployment succeeds, but `/health` 502 after smoke-test timeout
Container isn't starting. Inspect revision logs:

```bash
az containerapp logs show --name <acaApp> --resource-group <rg> \
  --container api --tail 200 --follow
```

Typical causes:

- Missing secret → `Settings.validate_production()` raised. Read the message; add the missing secret to Key Vault or to the Container App env vars.
- Migration failure on boot → the new schema conflicts with the DB state. Check Postgres logs; roll back to the previous revision.
- `AZURE_CLIENT_SECRET` wrong → Key Vault source can't auth; the banner shows "Key Vault source disabled", then `validate_production()` trips on the now-missing secret. Reset the SP credential.

### `Insufficient privileges` during Bicep deploy
The operator doesn't have `Owner` on the subscription. Key Vault role assignments require it. Ask for the role and retry.

### `The provided resource group already exists but is in a different location`
The RG exists in another region. Pick a different `resourceGroupName` or delete the existing RG first.

### `Key Vault name '<name>' already exists`
A prior deploy was torn down but the vault is in its 7-day soft-delete window. Either:

```bash
az keyvault purge --name <name>     # requires --purge-vault during destroy, or manual
# then re-run deploy
```

…or change `namePrefix` so a different vault name is generated.

---

## Runtime

### All requests 401 `AUTHENTICATION_ERROR`
- JWT secret mismatch — someone rotated `jwt-secret-key` in KV without restarting the app. Restart the revision.
- System clock drift — `exp` fails. Unlikely in ACA; check if a custom clock source is set.

### Specific endpoint returns 403 `AUTHORIZATION_ERROR: Permission required: …`
The caller's role doesn't grant that permission. Fix by reassigning their role or creating a custom role with the missing code. See [RBAC guides](../fe_admin_rbac_integration_guide.md).

### 429 `RATE_LIMIT_EXCEEDED` on every request
- Redis down → rate-limiter degrades OPEN, not closed. If you're seeing 429 it means Redis is up and real traffic is exceeding the org's quota. Raise `rate_limit_per_minute` via env var on the Container App.
- Single org sharing one API key across many integrations → each integration should have its own key so limits can be tuned per key.

### Scoring requests return `decision: ERROR` with `DE inference failed`
The DE Function App is down or the function key is wrong.

- Test directly: `curl -X POST "$DE_INFERENCE_URL?code=$DE_INFERENCE_CODE" -d '{}'`
- If 401 → rotate `de-inference-code` in KV and restart the app.
- If timeout → contact the ML team.

### `bureau_failed` on every scoring request
XDS is down or creds are wrong. Check:

```bash
# From the app container
curl -v https://www.online.xdsdata.com/XdsDataRestApi/Api/login
```

If the endpoint itself is unreachable, XDS is down (on their end). If you get a 401, rotate `xds-username` / `xds-password` in KV.

### Batch job stuck at `processing` forever
All replicas crashed or all 5 workers per replica died. Items are persisted in Redis (`batch:queue`) so no work is lost — restart the revision:

```bash
az containerapp revision restart --revision <latest> --name <acaApp> --resource-group <rg>
```

Then re-poll — workers drain the backlog.

### Emails never arrive
- Console mode still active (`EMAIL_PROVIDER=console`) — should be `resend` or `smtp` in production.
- Resend API key invalid → check the key in KV; test with a direct `curl https://api.resend.com/emails -H "Authorization: Bearer $KEY"`.
- Sender address not verified with Resend → verify the domain in the Resend dashboard.

### Postgres connections exhausted
`QueuePool limit of size 20 overflow 10 reached`. Either:

- Scale up the Postgres SKU (more `max_connections`) AND raise `database_pool_size` in env vars.
- Reduce `max_replicas` to fewer × replicas-worth-of-pool-size ≤ Postgres `max_connections` − 20 (leave headroom for admin connections).

### "Secret source: Azure Key Vault" but secrets are stale after rotation
The in-process cache keeps a fetched secret for the lifetime of the process. Rotating in KV requires a revision restart:

```bash
az containerapp revision restart --revision <latest> --name <acaApp> --resource-group <rg>
```

### `Service Bus subscriber received message it can't parse`
A topic contract changed. Check the DE/ML team's schema. The dead-letter queue retains bad messages for post-mortem:

```bash
az servicebus topic subscription dead-letter-queue-status-list \
  --namespace-name <sbNs> --resource-group <rg> \
  --topic-name scoring-complete --name backend-sub
```

---

## Migration-time

### `alembic upgrade head` reports a schema conflict
Preserve the deployment logs and compare the database's recorded revision with
the release migration chain. Then:

1. Run the migration locally against a DB snapshot.
2. Prepare and peer-review a forward repair migration where required.
3. Validate backup restore and migration execution in staging.
4. Redeploy the immutable image after approval.

The Argo PreSync/Container Apps migration gate keeps the previous application
revision serving until the schema step succeeds.

### `relation "<table>" already exists`
This indicates schema history and the Alembic version record require
reconciliation. Verify the table shape against the expected revision and take a
database backup before applying an approved repair. Use `alembic stamp` only
after confirming the complete target revision is already represented in the
database; stamping does not execute schema changes.

```bash
# Inspect recorded and available revisions
alembic current
alembic history --verbose
```

---

## Key Vault

### `ClientAuthenticationError: AADSTS7000215: Invalid client secret`
The SP secret rotated and the Container App's env var wasn't updated. See [runbook §Secret rotation](./runbook.md#secret-rotation).

### `ResourceNotFoundError` for a secret the app expects
The secret isn't in KV. Either the Bicep secret-seed module skipped it (check `main.bicep` outputs) or someone deleted it. Recreate:

```bash
az keyvault secret set --vault-name <kv> --name <kebab-case-name> --value <value>
```

### Key Vault soft-delete blocks re-deploy
```bash
az keyvault purge --name <kv>
```

---

## Getting help

1. Collect: deployment name (`DEPLOYMENT_NAME` echoed by `deploy.sh`), Container App revision name, Log Analytics snippet around the time of failure, output of `az containerapp show` at the moment.
2. Redact the parameters file of secrets before sharing.
3. Contact the delivery team with the bundle.
