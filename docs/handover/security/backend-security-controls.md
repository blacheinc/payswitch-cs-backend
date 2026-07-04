# Backend Security Controls Implemented

- **Prepared:** 30 June 2026
- **Scope:** PaySwitch backend application and supplied deployment assets
- **Status:** **Implemented and verified**

## Control position

The backend applies layered preventive, detective and resilience controls at
the identity, API, data, integration, runtime and delivery boundaries. Security
decisions are centralized in reusable dependencies and services so the same
policy is applied consistently across organisation and platform workflows.

The verified control set provides:

- authenticated and permission-scoped access;
- organisation isolation and audited privileged access;
- protected credentials, secrets and webhook messages;
- validated input, bounded external calls and abuse controls;
- traceable operations, isolated batch processing and least-privilege runtime execution.

## Control register

| Domain | Implemented control | Verification evidence |
|---|---|---|
| Authentication | Bcrypt password hashing | Security unit tests |
| Authentication | Signed, expiring access and refresh JWTs with explicit token types | Authentication tests |
| Authentication | TOTP MFA setup and verification | Security unit tests |
| Authentication | User/organisation status checks | Authentication matrix tests |
| Credentials | Cryptographically generated API keys; hashes retained at rest | Security tests and model review |
| Authorization | Central endpoint permission dependencies | Permission catalogue tests |
| Authorization | Separate platform and organisation permission scopes | System-role matrix tests |
| Tenant isolation | Organisation ID resolved from authenticated context | Auth/tenant tests |
| Privileged access | Dedicated platform permissions and cross-org audit records | Cross-org policy tests |
| Administration | Last-active-super-admin safeguard | Platform-role tests |
| Input protection | Typed Pydantic schemas, enums, ranges and UUID parsing | Schema and API tests |
| File processing | Supported-format parsing, mapping and type-quality checks | CSV/TSV/TXT/JSON/XLSX tests |
| Business controls | Seven deterministic hard-stop rules | 7/7 hard-stop tests |
| Replay protection | Redis-backed scoring idempotency | Cache/idempotency tests |
| Abuse protection | Per-organisation sliding-window rate limits | Rate-limit tests |
| Webhook integrity | HMAC-SHA256 signing and constant-time verification | Security tests |
| Secrets | Azure Key Vault source with approved environment fallback | Key Vault source tests |
| Configuration | Production startup validation | Configuration tests |
| Auditability | Request IDs, API logs, actor enrichment and privileged-access records | Middleware/enrichment tests |
| Privacy | Response-body exclusion from routine API logs | Middleware review |
| Messaging | Long-lived consumers with complete/abandon/dead-letter semantics | Event backend tests |
| Resilience | Per-item batch isolation and terminal status accounting | Worker/subscriber tests and E2E |
| External calls | Timeouts, TLS verification and controlled configured endpoints | Adapter review/tests |
| Supply chain | Dependency audit in local verification and GitLab CI | Zero known vulnerabilities |
| Static assurance | Ruff, mypy and Bandit release gates | Zero findings/errors |
| Container | Multi-stage runtime-only image and UID/GID 999 | Dockerfile and pipeline inspection |
| Kubernetes | Non-root, no privilege escalation, read-only root FS, dropped capabilities | Rendered-manifest assertions |
| Kubernetes | RuntimeDefault seccomp and resource requests/limits | Rendered manifests |
| Deployment | Argo PreSync migration/bootstrap job | Hook-policy assertion |
| Availability | Liveness, dependency-aware readiness, PDB and HPA | Manifest and health tests |
| CI/CD | Quality, coverage, SAST, dependency, image and Kustomize gates | `.gitlab-ci.yml` |

## Operational control integration

The supplied deployment material supports the client's established controls:

- TLS termination and approved ingress/WAF policy.
- Azure connection strings and Key Vault-backed secret lifecycle.
- Network-policy restriction to approved PostgreSQL, Redis and Azure endpoints.
- Registry retention and routine base-image refresh.
- Central log shipping, alert routing and access review.
- Managed PostgreSQL/Storage backup and recovery policy.

These controls are described in the deployment guide and can be applied through
the client's standard GitLab/Argo change-management process.
