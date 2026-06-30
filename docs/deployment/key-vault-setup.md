# Azure Key Vault Setup and Secret Operations

PaySwitch uses connection strings or access keys for Azure data-plane services.
Azure Key Vault itself authenticates with a dedicated service principal because
Key Vault does not provide a connection-string credential.

## Provisioned design

The supplied Bicep deployment:

1. Creates an RBAC-enabled Key Vault.
2. Grants the application service principal `Key Vault Secrets User`.
3. Optionally grants approved operator object IDs `Key Vault Secrets Officer`.
4. Stores database, Redis, Service Bus, Storage, JWT, bureau, function and email
   credentials under kebab-case secret names.
5. Injects the vault URL and service-principal identity into the application.

The operator provides the service principal; the deployment never creates or
exports an identity secret implicitly.

## Create the runtime service principal

```bash
az ad sp create-for-rbac --name "payswitch-kv-prod" --years 2
az ad sp show --id <appId> --query id -o tsv
```

Record the returned tenant ID, application/client ID and client secret in the
approved CI/CD secret store. The second command returns the principal object ID
required for the Key Vault role assignment.

Map the values into the Bicep parameters:

| Value | Parameter/runtime variable |
|---|---|
| Principal object ID | `appPrincipalObjectId` |
| Application/client ID | `appClientId` / `AZURE_CLIENT_ID` |
| Client secret | `appClientSecret` / `AZURE_CLIENT_SECRET` |
| Tenant ID | Container environment `AZURE_TENANT_ID` |
| Vault URI | Container environment `AZURE_KEYVAULT_URL` |

## Runtime resolution

[`app/config/keyvault_source.py`](../../app/config/keyvault_source.py) converts a
settings field such as `jwt_secret_key` to the Key Vault name
`jwt-secret-key`. Only allowlisted secret fields are queried.

Resolution order is:

1. Explicit `Settings(...)` constructor value used by controlled tests.
2. Azure Key Vault value when the vault URL and service-principal credential
   are configured.
3. Process environment value, providing the approved runtime fallback.
4. `.env` and file-secret sources in supported non-production workflows.

This allows the same image to use Key Vault in Azure or protected environment
variables in an approved Kubernetes secret workflow.

## Rotation

Rotate an application secret in Key Vault, then restart the workload so each
process refreshes its in-memory settings:

```bash
az keyvault secret set \
  --vault-name <vault-name> \
  --name <secret-name> \
  --value '<new-value>'

az containerapp revision restart \
  --resource-group <resource-group> \
  --name <app-name> \
  --revision <revision-name>
```

For Kubernetes, update Key Vault and allow External Secrets Operator to sync,
or update the protected Kubernetes Secret, then perform a rolling restart.

Rotate the Key Vault service-principal credential separately:

```bash
az ad sp credential reset --id <appId>
```

Update `AZURE_CLIENT_SECRET` in the workload's protected secret source before
restarting. Validate `/health`, `/ready` and one authenticated smoke test after
every rotation.

## Operational safeguards

- Keep parameter files containing real values outside source control.
- Use one service principal per environment.
- Grant the application read-only secret access.
- Grant secret-management rights only to approved operator groups.
- Enable Key Vault diagnostic logs and soft-delete/purge protection according
  to the client's Azure policy.
- Review secret access and credential expiry during scheduled access reviews.
