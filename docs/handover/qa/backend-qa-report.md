# Backend Internal QA Report

**Assessment date:** 30 June 2026  
**Component:** PaySwitch Credit Scoring Backend API  
**Overall result:** **Pass for backend handover**

## Scope and conclusion

The backend passed its repeatable source-quality, typing, automated-test,
coverage, dependency, static-security, manifest-rendering and deployment-policy
gates. The supplied one-shot verifier is `scripts/verify_handover.sh`.

This internal pass does not replace the client's environment acceptance test,
deployed DAST or the independent third-party VAPT.

## Verified results

| Gate | Evidence | Result |
|---|---|---|
| Ruff lint | `ruff check app tests scripts` | Pass; zero findings |
| Ruff format | `ruff format --check app tests scripts` | Pass |
| mypy | `mypy app tests` | Pass; zero errors |
| Automated tests | `pytest` | Pass; 88 tests |
| Overall coverage | `pytest --cov=app` | Pass; 60.61% (gate: 60%) |
| Critical-path coverage | focused security/auth/scoring/batch run | Pass; 88.71% (gate: 80%) |
| Dependency audit | `pip-audit` | Pass; zero known vulnerabilities |
| Static security | `bandit -r app -q` | Pass; zero findings |
| Kubernetes base | `kubectl kustomize infra/k8s/base` | Pass |
| Kubernetes example overlay | `kubectl kustomize infra/k8s/overlays/example` | Pass |
| Argo migration policy | rendered PreSync hook assertion | Pass |
| Pod least privilege | rendered `runAsNonRoot` assertion | Pass |
| Integration definition | `docker compose ... config --quiet` | Pass |

## Critical behavior covered

- Password hashing, JWT access/refresh tokens, TOTP and webhook signatures.
- Admin, organisation-user and API-key authentication paths.
- Permission gates, tenant scoping and audited platform-admin cross-org reads.
- All seven hard-stop rules and bureau signal extraction.
- XDS response parsing, reverse mapping and outbound inference payloads.
- Batch completion ingestion, deterministic demo worker success and terminal failure handling.
- File parsing/transformation across CSV, TSV, TXT, JSON and XLSX.
- Redis idempotency/cache behavior, rate limiting and fail-open handling.
- Service Bus/in-memory messaging behavior and training/scoring subscribers.
- Blob adapters, health/readiness checks, request metrics and API-log actor enrichment.

## Isolated E2E

`docker-compose.integration.yml` and `scripts/run_handover_e2e.sh` provide a
disposable Postgres/Redis/API environment. The script migrates, seeds RBAC and
demo users, authenticates an organisation user, submits three batch items, and
asserts two completed plus one deliberate terminal failure before teardown.

The compose definition validates successfully. Docker Desktop's engine returned
`EOF` on the assessment host, so the equivalent local isolated runner used a
temporary PostgreSQL 18 cluster and the integration-only in-process Redis mode.
All migrations ran, readiness passed, organisation authentication succeeded,
and the final batch state was exactly two completed and one failed with
`bureau_failed`. The runner removed its database and processes after success.
The Docker variant remains a required GitLab job and does not depend on XDS,
DE/AI, Azure or client credentials.

## Release recommendation

Backend engineering recommends this revision for handover and deployment into
the client's non-production AKS environment. Promotion to production remains
subject to the client's normal acceptance run, registry/image scan, deployed
DAST and independent VAPT sign-off.
