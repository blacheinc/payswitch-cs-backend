# Post-Deploy Checklist

Work through these after `./scripts/deploy.sh` reports success and `GET /health` returns 200.

---

## 1. Rotate the bootstrap admin password

The bootstrap admin credentials you supplied in `main.parameters.json` are now the only way into the system. **Rotate the password immediately.**

1. Open the dashboard at `https://<dashboardBaseUrl>`.
2. Log in with `bootstrapAdminEmail` / `bootstrapAdminPassword`.
3. Change the password via the profile page (or `PATCH /admin/admins/{id}` with a password rotation field when wired).
4. Delete the `bootstrap-admin-password` entry from Key Vault (optional — it's only read on cold boot when the admin doesn't exist yet).

---

## 2. Invite real admins

Use `POST /admin/admins` to invite every platform operator who needs access. See [admin RBAC guide](../fe_admin_rbac_integration_guide.md) for request shape and role matrix.

Typical first-day roster:

- 2 × `SUPER_ADMIN` — you + a named backup (avoid single-admin lockout).
- 1 × `RISK_ANALYST` per portfolio team.
- 1 × `MODEL_OPS_ENGINEER` per ML team member who needs training-data or champion access.
- 1 × `COMPLIANCE_OFFICER` per compliance officer.
- 1 × `INFRASTRUCTURE_ENGINEER` per SRE / on-call rotation member.

---

## 3. Smoke-test external integrations

Run each of these from a client machine or the dashboard:

| Integration | Verification |
|---|---|
| **Email (Resend/SMTP)** | Invite a throwaway admin — they should receive the temp-password email. Check spam. |
| **XDS Bureau** | Hit `POST /v1/bureau-lookup` with a known Ghana Card number. Non-2xx = creds wrong or XDS down. |
| **DE Inference** | Submit a scoring request via `POST /v1/score-requests` and poll the result. ERROR decision with DE timeout = function key wrong or URL unreachable. |
| **Orchestrator** | Call `POST /v1/rules/evaluate` from the dashboard's risk tab. 502 = function key wrong. |
| **Service Bus** | Tail Log Analytics for the three subscriber log lines at startup: `Subscribing to <topic> / <subscription>`. Missing → connection string unset or topic names mismatch. |

---

## 4. Tighten the Postgres firewall

The Bicep opens `AllowAllAzureServices` (start=0.0.0.0, end=0.0.0.0) on first create so the Container App can reach the DB without VNet plumbing. Once the app is running stably:

1. Find the Container App's outbound IP range:
   ```bash
   az containerapp show --name <acaApp> --resource-group <rg> \
     --query properties.outboundIpAddresses -o tsv
   ```
2. Replace the firewall rule:
   ```bash
   # Drop the catch-all
   az postgres flexible-server firewall-rule delete \
     --resource-group <rg> --name <pgServer> --rule-name AllowAllAzureServices

   # Add one rule per outbound IP
   az postgres flexible-server firewall-rule create \
     --resource-group <rg> --name <pgServer> --rule-name aca-<n> \
     --start-ip-address <ip> --end-ip-address <ip>
   ```

---

## 5. Bind a custom domain + TLS

Default FQDN is `<acaAppName>.<region>.azurecontainerapps.io`. To serve from `https://api.example.com`:

```bash
az containerapp hostname add \
  --hostname api.example.com \
  --resource-group <rg> \
  --name <acaApp>

az containerapp ssl upload \
  --hostname api.example.com \
  --resource-group <rg> \
  --name <acaApp> \
  --certificate-file cert.pfx \
  --certificate-password <pw>
```

Then update `ALLOWED_ORIGINS`, `DASHBOARD_BASE_URL`, and the dashboard's API client config to match.

---

## 6. Configure alerts

Recommended Log Analytics alert rules (create in the portal or via Azure Monitor Bicep):

| Condition | Severity |
|---|---|
| 5xx rate > 1% over 5 min | 2 (warning) |
| p99 latency > 2s over 5 min | 2 |
| Container App replica count = 0 | 0 (critical) |
| Postgres CPU > 80% over 10 min | 2 |
| Redis memory > 80% over 10 min | 2 |
| Service Bus dead-letter count > 10 | 2 |
| Repeated `AUTHENTICATION_ERROR` from the same IP | 3 (informational) |

The application-level [`Alert` table](../architecture/data-model.md#observability) produces dashboard-level alerts (feature drift, approval-rate shift, etc.) independently of these infra alerts.

---

## 7. Back-up verification

Postgres Flexible Server has automatic daily backups (7-day retention by default). Verify the restore path works **before** you need it:

```bash
# Point-in-time restore to a new server (takes 10-20 min)
az postgres flexible-server restore \
  --resource-group <rg> \
  --name <pgServer>-restore-test \
  --source-server <pgServer> \
  --restore-time "$(date -u -v-1H +%Y-%m-%dT%H:%M:%SZ)"

# Connect to the restored server, confirm data is there, then delete it.
```

Do this once post-deploy; repeat quarterly.

---

## 8. Rotate the service principal

The SP used for Key Vault access has a credential that expires in 2 years (per the `--years 2` in `az ad sp create-for-rbac`). Rotate yearly:

```bash
az ad sp credential reset --id <appId> --years 2
# Capture the new password
az containerapp update --name <acaApp> --resource-group <rg> \
  --set-env-vars AZURE_CLIENT_SECRET=<new-password>
```

The Container App's env-var update triggers a new revision; the old one stays active during rollover so there's no downtime.

---

## 9. Enable audit log retention

`ApiLog` and `AuditLog` grow unbounded. Decide on retention (typical: 365 days for AuditLog, 90 days for ApiLog) and add a nightly job — either an Azure Function on a CRON trigger or a `pg_cron` extension — that deletes rows older than the threshold.
