# Security

This document describes every security mechanism the backend enforces and the client operator's responsibilities for sustaining them. Scope is the FastAPI service and its dependencies — end-to-end HTTPS termination, WAF rules, and client-side concerns are out of scope and expected to be handled by the caller's edge infrastructure.

---

## 1. Authentication

Three principal types share a single `AuthContext`:

| Type | Auth mechanism | Where | Typical caller |
|---|---|---|---|
| Admin | JWT (HS256) | `Authorization: Bearer <jwt>` | Admin dashboard |
| Org user | JWT (HS256) | Same header | Client org dashboard |
| API key | SHA-256 hash lookup | Same header (value ≠ JWT) | Customer integrations |

### JWT

- **Algorithm**: HS256. Signing key is `settings.jwt_secret_key`, minimum 32 chars, sourced from Key Vault in production.
- **Access token lifetime**: 15 minutes (`access_token_expire_minutes`). Refresh token: 7 days.
- **Claims**: `sub` (user UUID), `type` (`access` / `refresh`), `user_type` (`admin` / `org_user`), `org_id` (org users only), `exp`, `iat`. The legacy `role` claim on admin JWTs is retained for FE back-compat but is informational; RBAC is not consulted from the JWT.
- **Refresh flow**: `POST /auth/refresh` with the refresh JWT. Tokens are not stored server-side — revocation is logout-on-device.
- **Password hashing**: bcrypt via `passlib`. 12 rounds.
- **2FA (admins only)**: TOTP with `pyotp`, per-admin `totp_secret`, verified at the login endpoint when `totp_enabled=True`.

### API keys

- **Format**: `sk_test_<base62>` or `sk_live_<base62>` (prefix in Settings).
- **Storage**: only `sha256(key)` is persisted (`ApiKey.key_hash`). The plaintext is returned once at creation time — the client is expected to store it securely.
- **Scoping**: every API key is bound to an `organization_id` and optionally to a specific `role_id`. When a role is assigned, the effective permission set is the intersection of the role's permissions and `ALL_ORG_PERMISSION_CODES` (a key can never get platform-scoped perms).
- **Revocation**: `DELETE /org/api-keys/{id}` flips status to `REVOKED` and records `revoked_at`. Subsequent requests fail at `get_auth_context` with 401.

---

## 2. Authorization (RBAC)

Two scopes, one permission catalog, one resolver:

- **`platform` scope** — 27 permissions, used by admin users. See [admin RBAC guide](../fe_admin_rbac_integration_guide.md).
- **`org` scope** — 27 permissions, used by org users and API keys. See [org RBAC guide](../fe_rbac_integration_guide.md).

Every endpoint declares its requirement inline:

```python
@router.post("/v1/score-requests")
async def create_score_request(
    auth: Annotated[AuthContext, Depends(require_permission("score_requests.create"))],
    ...
):
    ...
```

Missing permission → 403 with `Permission required: <code>`. Missing token → 401.

### System roles

- `SUPER_ADMIN` (platform) — all 27 platform permissions. Seeded with explicit `RolePermission` rows so introspection is accurate.
- `ADMIN` (org) — all 27 org permissions. Short-circuited to `ALL_ORG_PERMISSION_CODES` in the resolver for perf.
- `RISK_ANALYST`, `MODEL_OPS_ENGINEER`, `COMPLIANCE_OFFICER`, `INFRASTRUCTURE_ENGINEER` — the four operational platform roles. Permission sets in [`app/services/permission_seeder.py::SYSTEM_PLATFORM_ROLES`](../../app/services/permission_seeder.py).

System roles cannot be edited by API. The seeder reconciles their `RolePermission` rows on every boot (additive + prune).

### Custom roles

Callers with `admin.roles.manage` (platform) or `roles.manage` (org) can create, edit, and delete custom roles scoped to their plane. Custom roles can mix any permissions within the scope; the seeder does not touch them.

### Last-SUPER_ADMIN guard

Every suspend / demote / delete operation against a platform admin runs `_assert_not_last_super_admin` before committing. The platform cannot be locked out of administration by user error.

---

## 3. Secrets management

See [`app/config/keyvault_source.py`](../../app/config/keyvault_source.py) and [`docs/deployment/key-vault-setup.md`](../deployment/key-vault-setup.md).

- **Production**: 12 secrets live in Azure Key Vault, read on boot by a pydantic-settings custom source using a pre-provisioned service principal (`AZURE_CLIENT_ID` + `AZURE_CLIENT_SECRET` + `AZURE_TENANT_ID` as env vars).
- **Development**: the KV source is a no-op when `AZURE_KEYVAULT_URL` is unset. All secrets then fall through to env vars / `.env` file.
- **Naming convention**: Settings field `jwt_secret_key` ↔ KV secret `jwt-secret-key` (snake → kebab).
- **Allowlist**: only fields in `SECRET_FIELDS` are ever fetched from KV. URLs and feature flags are env-only.
- **Cache**: per-process SDK client cache + per-request secret cache; first-boot adds ~1–3 s for 12 secrets.
- **Fail-open fallback**: KV errors are logged and the source returns `None` — env vars take over. A misconfigured KV does NOT bring the app down.
- **Service principal rotation**: `az ad sp credential reset --id <appId>` then update `AZURE_CLIENT_SECRET` on the Container App. The SDK does not cache credentials across processes, so a restart picks up the new secret.

---

## 4. Transport security

| Hop | Protection |
|---|---|
| Client → API | TLS terminated by Azure Container Apps (`allowInsecure: false`) |
| API → Postgres | `sslmode=require` in the connection string |
| API → Redis | `rediss://` scheme (TLS) |
| API → Service Bus | AMQP over TLS |
| API → Blob Storage | HTTPS + `supportsHttpsTrafficOnly: true` on the account |
| API → Key Vault | HTTPS + AAD |
| API → XDS / DE / Orchestrator | HTTPS |

Postgres + Storage force TLS 1.2 minimum. Redis forces minimum TLS 1.2 + disables the non-TLS port.

---

## 5. Network posture

Initial deploy is **public-endpoint-with-credentials** — every Azure service is reachable on its public FQDN, gated by its connection string / access key. This is a deliberate v1 tradeoff that keeps the Bicep self-contained and doesn't assume a client VNet design.

Hardening paths once the client has a preferred topology:

1. **Postgres firewall**: the Bicep opens `AllowAllAzureServices` on create. After the first successful boot, record the Container App's outbound IP ranges and tighten via `firewallStartIp` / `firewallEndIp` (or switch to VNet integration).
2. **Redis private endpoint**: Basic SKU doesn't support private endpoints; upgrade to Standard or Premium if this matters.
3. **Storage + Key Vault**: enable `networkAcls.defaultAction=Deny` + add the Container App's outbound IP or VNet subnet. Safe post-boot; breaking change during first deploy.
4. **Service Bus private endpoint**: Standard SKU supports it; requires VNet integration on the Container App.

---

## 6. Audit + API logging

- **`ApiLog`** — middleware records every HTTP call: method, path, status, response time, IP, user-agent, `api_key_id`, `user_id`, request_id, error detail on 4xx/5xx. Retention is operator-configurable; recommended 90 days for compliance.
- **`AuditLog`** — append-only actor-centric log of semantically important actions (user invited, role changed, organization suspended, API key created, scoring decision overridden). Always includes `actor_type`, `actor_id`, `actor_email`, `action`, `resource_type`, `resource_id`, `changes` (JSONB).
- **`RequestMetric`** — infrastructure latency/status counters feeding the Infrastructure Dashboard.
- **`DecisionMetric`, `ModelMetric`, `Alert`** — ML observability, feeding the Risk / ModelOps / Compliance dashboards.

Every log carries the per-request `request_id` injected by `RequestIdMiddleware`, correlatable across all tables and with Log Analytics.

---

## 7. Rate limiting

Redis-backed sliding window, keyed by `organization_id`, enforced before the permission check. Configuration in Settings:

| Setting | Default | Scope |
|---|---|---|
| `rate_limit_per_minute` | 100 | Per org |
| `rate_limit_per_day` | 50_000 | Per org |
| `rate_limit_burst` | 200 | Per org |

429 responses include a `Retry-After` header.

---

## 8. CORS

`ALLOWED_ORIGINS` env var is a comma-separated allowlist. Empty list means no browser origin can make a preflight-bearing request. `allow_credentials: true` (to carry JWTs in browsers).

---

## 9. Input validation

All request bodies are Pydantic models with explicit field validators. Fields that end up in external queries (scoring payloads, batch items) are cast to their declared types before any DB write or outbound call. JSONB blobs that mirror untrusted third-party payloads (bureau responses) are never rendered unescaped into HTML.

---

## 10. Responsible-disclosure contract

The backend does not implement its own vulnerability-reporting workflow. The client's security org should publish a `security.txt` alongside the dashboard hostname pointing at their intake channel. Standard hardening tasks that belong to the operator, not the code:

- Azure Defender for Cloud + Defender for Containers enabled on the subscription.
- Log Analytics alert rules on `ApiLog` 5xx spikes and repeated `AUTHENTICATION_ERROR`.
- Key Vault diagnostic settings sending audit logs to Log Analytics.
- Quarterly rotation of the app's service-principal secret.
- Annual recreation of `jwt_secret_key` (all sessions invalidated — coordinate with users).
