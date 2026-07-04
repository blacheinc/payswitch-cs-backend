# Deployment Guide

> Target: a client-owned Azure subscription. You are the operator running this deploy; PaySwitch's delivery team hands you the code and this guide, and you drive everything from here.

This document is the table of contents for the operational docs. If you're deploying for the first time, work through sections 1–4 in order.

---

## 1. Architecture at a glance

Read [`docs/architecture/overview.md`](../architecture/overview.md) first. One FastAPI container backed by Postgres / Redis / Service Bus / Storage / Key Vault. Everything provisioned by one Bicep template; everything deployable with one shell script.

> **Deploying to Kubernetes instead?** The same image and environment contract work on Kubernetes. See [`k8s-overview.md`](./k8s-overview.md) for the integration architecture and [`infra/k8s/README.md`](../../infra/k8s/README.md) for the Kustomize manifests, Argo CD application and GitLab CI reference. The remainder of this guide covers the Azure Container Apps target.

---

## 2. Prerequisites

1. **Azure subscription** you own. The operator needs `Owner` role on the subscription for the initial deploy (Key Vault RBAC role assignments require it).
2. **Azure CLI** ≥ 2.60. Install: <https://learn.microsoft.com/cli/azure/install-azure-cli>
3. **Bicep extension** for the CLI. The deploy script installs it for you if missing.
4. **Service principal** used by the running API to read Key Vault. Create it once per environment:

   ```bash
   az ad sp create-for-rbac --name "payswitch-kv-prod" --years 2
   # → capture {appId, password, tenant}

   az ad sp show --id <appId> --query id -o tsv
   # → this is the SP's objectId (NOT the same as appId) — needed by the Bicep
   ```

   Keep the three identifiers + password safe; they go into `main.parameters.json` in step 3.
5. **External-service credentials** from the respective teams (XDS bureau, DE inference function key, orchestrator function key, email provider API key). See the parameters table in `infra/bicep/README.md`.

---

## 3. Configure parameters

Edit [`infra/bicep/main.parameters.json`](../../infra/bicep/main.parameters.json). Every placeholder in angle brackets (`<…>`) must be replaced before you deploy. The parameter reference is in [`infra/bicep/README.md`](../../infra/bicep/README.md#parameters).

Critical placeholders:

| Parameter | How to get the value |
|---|---|
| `appPrincipalObjectId` / `appClientId` / `appClientSecret` | From the `az ad sp create-for-rbac` step above |
| `postgresAdminPassword` | Generate: `openssl rand -base64 32` (must contain upper/lower/digit) |
| `jwtSecretKey` | Generate: `openssl rand -hex 32` |
| `xdsUsername` / `xdsPassword` | From the credit bureau team |
| `deInferenceUrl` + `deInferenceCode` | From the ML/DE team (their function app) |
| `orchestratorBaseUrl` + `orchestratorFunctionKey` | From the rule-engine team |
| `dashboardBaseUrl` | The dashboard hostname you'll own, e.g. `https://dashboard.example.com` |
| `emailFrom` | A sender address you've verified with Resend or your SMTP provider |
| `bootstrapAdminEmail` / `bootstrapAdminPassword` | First-time admin login — rotate immediately after first login |

> **Never commit this file with real values.** Pass sensitive parameters on the command line or via a parameter override file stored outside the repo.

---

## 4. Deploy

```bash
# Log in + select the target subscription
az login
az account set --subscription <sub-id>

# One-shot deploy (10–15 min cold, mostly Postgres)
./scripts/deploy.sh
```

What the script does, in order:

1. Preflight (az installed, logged in, params file present).
2. `bicep validate` + `what-if` preview; you confirm.
3. `az deployment sub create` — provisions every Azure resource + seeds Key Vault.
4. `az acr build` — builds and pushes the API image directly in ACR (no local Docker needed).
5. `az containerapp update --image` — rolls out the new image; waits for the revision to become Healthy.
6. `curl https://<fqdn>/health` — smoke test.

If step 6 succeeds, you're live.

---

## 5. After a successful deploy

[`post-deploy.md`](./post-deploy.md) — covers:

- First-time admin login and password rotation.
- Inviting additional admins through the API.
- Tightening the Postgres firewall.
- Binding a custom domain + TLS cert to the Container App.
- Smoke-testing every external integration (bureau, DE, orchestrator, email).

---

## 6. Day-2 operations

[`runbook.md`](./runbook.md) — covers:

- How to scale (replicas, SKU tiers).
- Where to find logs (Log Analytics + `ApiLog` table).
- How to run Alembic migrations on a live deploy.
- Rotating the service principal secret.
- Disaster recovery: restore Postgres from backup.

[`key-vault-setup.md`](./key-vault-setup.md) — covers the service-principal,
secret resolution, rotation and Kubernetes integration contract.

---

## 7. When things go wrong

[`troubleshooting.md`](./troubleshooting.md) — operational diagnostics and recovery procedures.

---

## 8. Subsequent deploys

Code-only redeploy (no infra changes):

```bash
./scripts/deploy.sh --skip-infra --yes
```

Infra-only redeploy (e.g. after editing `main.parameters.json` or a Bicep module):

```bash
./scripts/deploy.sh --skip-build
```

## 9. Tearing down

```bash
./scripts/destroy.sh --purge-vault   # asks you to type the RG name to confirm
```

Deletes the resource group and, optionally, purges the soft-deleted Key Vault so its name can be reused immediately.
