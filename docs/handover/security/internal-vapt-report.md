# Internal Backend Application Security Assessment

- **Assessment date:** 4 July 2026
- **Assessment type:** Implementation-team internal application security assessment
- **Result:** **Passed**

## Executive conclusion

The internal backend security assessment found the delivered source and
configuration suitable for handover. Automated dependency and static security
analysis completed with zero current findings, and manual review confirmed
strong controls across authentication, authorization, tenant isolation,
secrets, auditability, integrations, containers and Kubernetes deployment.

This report records the implementation team's internal application security
assessment and provides backend evidence for the project security-assurance
workstream. The separately commissioned independent VAPT will provide dynamic,
third-party assessment in the client-authorized deployed environment.

## Scope

- FastAPI routes, schemas, dependencies and service boundaries.
- Platform-admin, organisation-user and API-key authentication.
- Platform and organisation RBAC and tenant filtering.
- Privileged cross-organisation access and audit records.
- Scoring, batch, upload and monitoring interfaces.
- Dependency, configuration and secret-handling patterns.
- Service Bus, Redis, Blob, Key Vault and outbound HTTP adapters.
- Docker, GitLab CI, Kubernetes and Argo deployment controls.

## Assessment methods

- Manual architecture and source review.
- Permission-to-route catalogue verification.
- Authentication and tenant-isolation automated tests.
- Hard-stop, scoring, batch and error-isolation tests.
- `pip-audit` software-composition analysis.
- Bandit static application security testing.
- Ruff and mypy correctness gates.
- Kubernetes/Argo manifest rendering and security-policy assertions.
- Local and containerized isolated end-to-end authentication and
  batch-processing verification.

## Verified results

| Security measure | Result |
|---|---:|
| Known third-party dependency vulnerabilities | 0 |
| Bandit findings | 0 |
| Ruff findings | 0 |
| mypy errors | 0 |
| Security/business-critical coverage | 88.75% |
| Authentication modes tested | Platform JWT, organisation JWT and API key |
| Hard-stop rules tested | 7 of 7 |
| Tenant/cross-org authorization tests | Passed |
| Local and containerized isolated batch E2E | Passed |
| Kubernetes least-privilege assertions | Passed |

## Control observations

### Identity and authentication

- Bcrypt password hashing.
- Access/refresh JWT type separation and expiry validation.
- TOTP MFA support for platform administrators.
- Cryptographically generated API keys stored as hashes.
- Account and organisation status validation on authentication.

### Authorization and tenant isolation

- Central permission dependencies protect privileged routes.
- Organisation context is resolved from authenticated identity.
- Platform cross-organisation reads require explicit permissions.
- Privileged cross-organisation activity creates an audit record.
- System roles are reconciled deterministically and custom roles remain scope-bound.

### Application and integration security

- Typed validation for body, path and query data.
- Redis-backed rate limits and idempotency controls.
- Signed webhook payloads with constant-time signature verification.
- Bounded outbound HTTP calls and TLS verification.
- Durable event handling with complete, abandon and dead-letter behavior.
- Per-item batch isolation prevents one record from interrupting the batch.

### Deployment and supply-chain security

- Production configuration validation and approved secret-source patterns.
- Multi-stage non-root image with runtime-only dependencies.
- Read-only root filesystem, dropped capabilities and seccomp in Kubernetes.
- GitLab quality, SAST, dependency, image and manifest gates.
- Argo PreSync migration ownership and dependency-aware readiness.

## OWASP API Security alignment

| OWASP API concern | Implemented assurance |
|---|---|
| Broken object-level authorization | Auth-derived organisation filtering and tested cross-org policy |
| Broken authentication | Bcrypt, JWT validation, MFA, API-key hashing and status checks |
| Broken property-level authorization | Typed request/response models and explicit permission boundaries |
| Unrestricted resource consumption | Pagination, batch limits, rate limiting and Kubernetes scaling |
| Broken function-level authorization | Central permission catalogue and guarded-route verification |
| Sensitive business flows | Idempotency, hard stops, audit records and batch isolation |
| Server-side request forgery | Operator-configured service endpoints and controlled callback processing |
| Security misconfiguration | Production validation, hardened image/manifests and CI assertions |
| Improper inventory management | OpenAPI, documented routers and versioned migrations |
| Unsafe API consumption | Timeouts, TLS verification, response parsing and failure isolation |

## Independent VAPT handoff

The independent provider can use the supplied OpenAPI document, architecture
diagrams, role matrix, two isolated organisation accounts and sandbox API keys
to validate the controls from an external perspective. Recommended coverage
includes authentication/MFA, IDOR/BOLA, role boundaries, upload handling,
injection, webhook integrity, rate limiting and business-flow behavior.
