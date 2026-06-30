# Internal Backend VAPT Report

**Assessment date:** 30 June 2026  
**Assessment type:** Internal source/configuration security review  
**Independence:** **Not independent** — performed by the implementation team  
**Overall rating:** **Internal source/configuration assessment passed**;
credential rotation, deployed DAST and independent VAPT remain external release actions

## 1. Scope

- FastAPI application source, dependencies and configuration.
- Authentication, RBAC, tenant scoping and privileged cross-org reads.
- Secret handling and deployment artefacts.
- Container runtime construction.
- Migration/bootstrap execution model.

Excluded: cloud-account configuration, network perimeter, AKS control plane,
live XDS/AI endpoints, frontend, social engineering, denial-of-service load
testing and destructive database tests.

## 2. Methods and tools

- Manual source and architecture review.
- Bandit static security scan.
- pip-audit software-composition analysis.
- Pattern-based repository secret scan.
- Ruff undefined-name/security-adjacent checks.
- Docker image build and runtime identity inspection.
- Existing RBAC/Key Vault test suite.

No automated web scanner was pointed at a live environment in this assessment.

## 3. Executive results

| Category | Before remediation | After remediation |
|---|---:|---:|
| Critical findings | 1 | 0 in code; rotation action remains |
| High findings | 4 | 0 open in the assessed source; DAST is external scope |
| Medium findings | 3 | 1 operational risk accepted with compensating control |
| Known dependency advisories | 26 | 0 |
| Bandit findings (all severities) | 10 | 0 |

## 4. Findings

### VAPT-01 — Hardcoded credentials in repository

**Severity:** Critical  
**Status:** Removed from current tree; credential rotation pending

XDS, administrator, organisation-user and Azure Function test credentials were
embedded in executable scripts. Anyone with repository or historical clone
access may have obtained them.

**Remediation:** all scripts now read credentials from environment variables.
Replacement credentials must be issued because deleting text from the current
branch does not erase prior Git objects.

### VAPT-02 — Vulnerable dependency versions

**Severity:** High  
**Status:** Closed

The initial audit reported 26 advisories across cryptography, IDNA, pip,
pydantic-settings, PyJWT, python-multipart, Starlette and urllib3. Minimum safe
versions were added to `pyproject.toml`; the build upgrades pip; the final audit
reported no known vulnerabilities.

### VAPT-03 — Over-privileged production container

**Severity:** High  
**Status:** Closed

The original image installed development dependencies and ran as root. The new
multi-stage image installs only runtime wheels, drops compiler packages and
runs as UID/GID 999. Runtime imports were verified.

### VAPT-04 — Runtime errors in privileged training-data routes

**Severity:** High  
**Status:** Closed

Several routes used an undefined `admin` variable when assigning uploader or
approver identities. These references now use the authenticated `AuthContext`.
Ruff reports zero remaining undefined names.

### VAPT-05 — Migration race in Kubernetes

**Severity:** High  
**Status:** Closed

The application entrypoint and Argo PreSync Job could both execute migrations.
Startup flags now disable migrations/bootstrap in API pods; the PreSync Job is
the single Kubernetes owner.

### VAPT-06 — Insufficient automated coverage

**Severity:** High  
**Status:** Closed

The suite now contains 88 tests and enforces 60% whole-application coverage.
A separate critical-path gate covers password/JWT/TOTP security, authentication
dependencies, hard stops, scoring-pipeline mapping and batch-result ingestion
at 88.71% against an 80% minimum.

### VAPT-07 — Rate-limit fail-open behaviour

**Severity:** Medium  
**Status:** Conditional acceptance

When Redis is unavailable, authenticated requests continue. This preserves
availability but removes application-layer abuse protection. Production must
also enforce limits at ingress/API gateway/WAF and alert on the fail-open log.

### VAPT-08 — Static quality/type debt

**Severity:** Medium  
**Status:** Closed

Ruff reports zero findings and mypy reports zero errors across application and
test code. Both checks are blocking jobs in the supplied GitLab pipeline.

## 5. OWASP API Security mapping

| OWASP API risk | Relevant implementation/control | Further test required |
|---|---|---|
| API1 Broken Object Level Authorization | Organisation filters, RBAC dependencies, audited cross-org access | DAST with two-org accounts |
| API2 Broken Authentication | Bcrypt, JWT type/expiry checks, TOTP, account status checks | Brute force, refresh replay and reset-token tests |
| API3 Broken Object Property Authorization | Pydantic request schemas, explicit response models | Mass-assignment fuzzing |
| API4 Unrestricted Resource Consumption | Redis rate limits, pagination, upload validation | Upload-size and concurrency load tests |
| API5 Broken Function Level Authorization | Permission catalogue and endpoint dependencies | Full role/route matrix test |
| API6 Sensitive Business Flows | Idempotency, batch limits, audit logging | Automated abuse scenarios |
| API7 SSRF | Configured outbound endpoints; no user-controlled base URLs identified | URL-field and callback validation review |
| API8 Security Misconfiguration | Production validation, non-root image, Key Vault/environment secrets | Cluster, ingress and header scan |
| API9 Inventory Management | OpenAPI plus documented routers | Compare deployed routes/version to inventory |
| API10 Unsafe API Consumption | Timeouts and schemas around XDS/DE services | Malformed/oversized upstream response tests |

## 6. Independent VAPT handoff scope

The third party should receive a staging URL, OpenAPI document, architecture
diagram, test accounts for all five platform roles and at least two isolated
organisations, API keys for sandbox scope, and explicit authorization to test
cross-tenant access. They should validate authentication, reset/MFA flows,
IDOR/BOLA, file upload, SSRF, injection, webhook signatures, rate limits,
business-flow abuse and error leakage, then issue a signed retest report.
