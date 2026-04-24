# PaySwitch Credit Scoring — Azure IaC

One-shot Bicep that provisions every Azure resource the backend needs:

- **Resource group** (created by the deployment)
- **Log Analytics workspace** — backs container logs
- **Azure Container Registry** (Basic) — holds the API image
- **Storage account** (Standard LRS) — 4 containers: `bulk-uploads`, `training-data`, `reports`, `model-artifacts`
- **PostgreSQL Flexible Server** — burstable `B2ms`, `credit_scoring` database, AllowAllAzureServices firewall rule
- **Azure Cache for Redis** (Basic C0) — rate limiting, idempotency, batch queue
- **Service Bus namespace** (Standard) — 6 topics + 3 subscriptions matching the app's lifespan handlers
- **Key Vault** — holds every runtime secret, accessed by the app via a service-principal credential
- **Container Apps environment + Container App** — the API, scaled by HTTP concurrency

All runtime secrets (connection strings, JWT key, XDS creds, function keys, bootstrap admin password) are written into Key Vault by the same deployment. The Container App is launched with a **placeholder image**; the real image is built and swapped in by the companion deploy script after the Bicep completes (Phase D).

> **Auth model**: every data-plane service is accessed via connection strings / access keys (Postgres password, Redis access key, Service Bus connection string, Storage key, ACR admin creds). Key Vault is the only Azure service that requires AAD — we use a service principal there as the closest "static credential" equivalent.

---

## Prerequisites

1. An Azure subscription and an operator with `Owner` on the target subscription (required to assign the Key Vault RBAC roles).
2. `az` CLI ≥ 2.60 and the Bicep extension (`az bicep install`).
3. A **service principal** the running API will use to read Key Vault. Create once per environment:

   ```bash
   az ad sp create-for-rbac \
     --name "payswitch-credit-scoring-prod" \
     --years 2
   # → capture appId, password, tenant (you need all three)

   az ad sp show --id <appId> --query id -o tsv
   # → this is the SP's objectId (NOT the appId) — needed by the Bicep
   ```

   Capture:
   - `appId`           → `appClientId` parameter
   - `password`        → `appClientSecret` parameter (secure)
   - SP's `objectId`   → `appPrincipalObjectId` parameter

4. Optionally, the operator's own AAD `objectId` if they want browse/rotate permissions on Key Vault — pass it in `adminObjectIds`.

---

## Parameters

Every parameter lives in [`main.parameters.json`](./main.parameters.json). Edit that file or override on the command line with `--parameters <name>=<value>`. Placeholders in angle brackets (`<…>`) must be filled in.

| Parameter | Required | Secret | Purpose |
|---|:-:|:-:|---|
| `location` | ✅ | — | Azure region, e.g. `westeurope` |
| `resourceGroupName` | ✅ | — | Created if missing |
| `namePrefix` | ✅ | — | 3–12 lowercase alphanum, used as the stem for every resource name |
| `environment` | ✅ | — | `dev` / `stg` / `prod` |
| `appPrincipalObjectId` | ✅ | — | SP objectId (NOT appId) |
| `appClientId` | ✅ | — | SP appId |
| `appClientSecret` | ✅ | 🔐 | SP password |
| `adminObjectIds` | — | — | Array of AAD objectIds granted Key Vault Secrets Officer |
| `postgresAdminLogin` | ✅ | — | DB admin login name |
| `postgresAdminPassword` | ✅ | 🔐 | DB admin password (8+ chars, mixed case/digits/symbols) |
| `jwtSecretKey` | ✅ | 🔐 | Random ≥ 32 chars; `openssl rand -hex 32` |
| `xdsUsername` | ✅ | 🔐 | Credit bureau login |
| `xdsPassword` | ✅ | 🔐 | Credit bureau password |
| `resendApiKey` | — | 🔐 | Blank to fall back to SMTP/console email |
| `deInferenceUrl` | ✅ | — | DE Function App URL |
| `deInferenceCode` | ✅ | 🔐 | DE function host key |
| `orchestratorBaseUrl` | ✅ | — | Orchestrator function URL |
| `orchestratorFunctionKey` | ✅ | 🔐 | Orchestrator function host key |
| `customerServiceBaseUrl` | — | — | Optional downstream service URL |
| `customerServiceFunctionKey` | — | 🔐 | Optional downstream service key |
| `dashboardBaseUrl` | ✅ | — | `https://dashboard.<client-domain>` — embedded in email links |
| `emailFrom` | ✅ | — | Must be verified with the email provider |
| `allowedOrigins` | — | — | CORS origins (comma-separated) |
| `bootstrapAdminEmail` | — | — | First-boot admin email; blank skips seeding |
| `bootstrapAdminPassword` | — | 🔐 | First-boot admin password — rotate immediately after first login |

**Never commit** secret values. Use one of:

- `--parameters "<name>=<value>"` on the command line (one per secret).
- `az keyvault secret` + `az deployment sub create --parameters @main.parameters.json --parameters jwtSecretKey=@<vault-reference>`.
- A parameter JSON file stored outside the repo (e.g. in a protected CI secret).

---

## Deploy

```bash
# 1. Log in and pick the target subscription
az login
az account set --subscription <sub-id>

# 2. (First time only) install the Bicep CLI extension
az bicep install

# 3. Validate the template
az deployment sub validate \
  --location westeurope \
  --template-file infra/bicep/main.bicep \
  --parameters @infra/bicep/main.parameters.json

# 4. Dry-run to preview the changes
az deployment sub what-if \
  --location westeurope \
  --template-file infra/bicep/main.bicep \
  --parameters @infra/bicep/main.parameters.json

# 5. Deploy
az deployment sub create \
  --name payswitch-bootstrap \
  --location westeurope \
  --template-file infra/bicep/main.bicep \
  --parameters @infra/bicep/main.parameters.json
```

The deployment takes ~10–15 minutes on a cold subscription (Postgres is the slowest resource).

---

## Outputs

After a successful deploy, capture:

```bash
az deployment sub show --name payswitch-bootstrap \
  --query properties.outputs -o json
```

You'll get:

- `acrLoginServer` — the registry the deploy script pushes to
- `acrName`
- `acaAppName`
- `acaAppFqdn` — public URL of the API (e.g. `https://psc-prod-api…azurecontainerapps.io`)
- `keyVaultUri` / `keyVaultName`
- `postgresFqdn`
- `storageAccountName`

The companion deploy script (`scripts/deploy.sh`, Phase D) reads these outputs to build+push the image and swap it onto the Container App.

---

## Post-deploy checklist

1. **Swap in the real image** — the Bicep ships a placeholder. Run `scripts/deploy.sh` (Phase D) or the equivalent `az acr build` + `az containerapp update --image`.
2. **Verify the API** — `curl https://<acaAppFqdn>/health` returns `{"status":"ok", …}`.
3. **First-time login** — the bootstrap admin credentials you provided are in KV as `bootstrap-admin-password`. Log into the dashboard with them and **rotate immediately**.
4. **Rotate the SP secret** periodically — `az ad sp credential reset --id <appId>` and update `AZURE_CLIENT_SECRET` on the Container App.
5. **Tighten Postgres firewall** — the initial deploy opens "AllowAllAzureServices"; restrict to the Container App's outbound IPs (via `firewallStartIp` / `firewallEndIp` params) once you've captured them from the environment.
6. **Enable TLS certificate on a custom domain** — bind your own domain via `az containerapp hostname` if the generated `azurecontainerapps.io` FQDN isn't acceptable.

---

## Teardown

```bash
az group delete --name <resourceGroupName> --yes --no-wait
# Key Vault soft-delete keeps the vault for 7 days; to hard-purge:
az keyvault purge --name <keyVaultName>
```
