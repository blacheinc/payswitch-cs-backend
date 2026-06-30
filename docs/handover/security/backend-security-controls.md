# Backend Security Controls Implemented

**Prepared:** 30 June 2026  
**Scope:** application/backend and supplied deployment artefacts

| Domain | Control | Implementation/evidence | Status |
|---|---|---|---|
| Authentication | Password hashing | Bcrypt through Passlib | Implemented |
| Authentication | Short-lived access tokens | Signed JWT with explicit algorithm and token type | Implemented |
| Authentication | Refresh tokens | Separate type, expiry and random JTI | Implemented |
| Authentication | Platform MFA | TOTP setup/verification with bounded clock window | Implemented |
| Credentials | API-key protection | Cryptographically random keys; only SHA-256 hash retained | Implemented |
| Authorization | Endpoint RBAC | Central `require_permission`/`require_any_permission` dependencies | Implemented |
| Authorization | Tenant isolation | Organisation ID derived from authenticated context | Implemented |
| Authorization | Privileged reads | Separate platform permissions and audit trail for cross-org access | Implemented |
| Administration | Last-super-admin safety | Prevents removal/demotion of final active super admin | Implemented |
| Input safety | Schema validation | Pydantic validation, enums, lengths, ranges and UUID parsing | Implemented |
| Input safety | Upload restrictions | File-format checks and parsing validation | Implemented; size limit should also exist at ingress |
| Abuse prevention | Rate limiting | Redis sliding windows per organisation | Implemented; fail-open risk documented |
| Replay prevention | Idempotency | Redis-backed idempotency keys on scoring flow | Implemented |
| Secrets | No production-realistic defaults | Production startup validation rejects missing settings | Implemented |
| Secrets | Secret sources | Azure Key Vault source with environment fallback and allow-list | Implemented |
| Secrets | Repository hygiene | `.env*` ignored; test credentials removed from current tree | Implemented; rotation pending |
| Cryptography | Webhook integrity | HMAC-SHA256 signing and constant-time verification | Implemented |
| Audit | Request/API logs | Request ID, actor/org context, status, latency, IP and user-agent | Implemented |
| Audit | Privileged access records | Cross-org score/batch reads create audit rows | Implemented |
| Availability | External-call limits | Timeouts configured for XDS and HTTP clients | Implemented |
| Availability | Health probes | `/health` used by Container Apps and Kubernetes probes | Implemented |
| Messaging | Long-lived Service Bus consumers | Avoids reconnect storm; failures abandon messages for retry | Implemented |
| Supply chain | Dependency scanning | `pip-audit` added to dev tooling; safe version floors | Implemented |
| Supply chain | Static security scanning | Bandit added to dev tooling | Implemented |
| Supply chain | Enforced quality gates | GitLab blocks on Ruff, formatting, mypy and coverage | Implemented |
| Supply chain | Container vulnerability scan | GitLab Trivy job blocks fixable high/critical image findings | Implemented |
| Container | Least privilege | Multi-stage image, runtime-only dependencies, UID/GID 999 | Implemented |
| Kubernetes | One-shot migration ownership | Argo PreSync Job; API replicas disable migration/bootstrap | Implemented |
| Kubernetes | Pod least privilege | Non-root UID/GID, read-only root FS, dropped capabilities, no privilege escalation and RuntimeDefault seccomp | Implemented |
| Transport | TLS | Expected at ingress and all Azure/external service connection strings | Deployment responsibility |
| Network | Exposure control | Service/Ingress/namespace manifests supplied | Client cluster responsibility |
| Backup/DR | PostgreSQL and blob backup | Procedures documented in deployment runbook | Client platform responsibility |

## Control limitations and required compensating controls

1. Because rate limiting fails open, enforce an ingress/API-gateway limit and
   alert on `Rate limiter unavailable` messages.
2. Use ingress request-body limits for upload and JSON payloads.
3. Restrict CORS to exact client origins; never use `*` with credentials.
4. Store replacement secrets in the client's approved vault and rotate all
   values previously present in Git history.
5. Apply Kubernetes NetworkPolicies and least-privilege service accounts in the
   client's cluster overlay.
6. Run image, manifest and DAST scanning in GitLab before Argo promotion.
