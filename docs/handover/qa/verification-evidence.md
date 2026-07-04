# Backend Verification Evidence

- **Executed:** 4 July 2026
- **Migration head:** `a9b0c1d2e3f4`
- **Outcome:** **All handover gates passed**

## Repeatable quality and security gate

```bash
./scripts/verify_handover.sh
```

| Check | Recorded result |
|---|---:|
| Ruff lint | Zero findings |
| Ruff formatting | 126 files correctly formatted |
| mypy | Zero errors across 112 source/test files |
| pytest | 88 passed |
| Whole-application coverage | 60.62%; 60% required |
| Critical-path coverage | 88.75%; 80% required |
| Dependency audit | Zero known vulnerabilities |
| Bandit | Zero findings |
| Kustomize base/overlay | Rendered successfully |
| Argo PreSync migration assertion | Passed |
| Kubernetes non-root assertion | Passed |
| Integration Compose validation | Passed |

`pip-audit` correctly identifies the local PaySwitch package as a first-party
project rather than a PyPI dependency; all resolved third-party packages are
included in the audit.

## Local isolated end-to-end acceptance

```bash
./scripts/run_handover_e2e_local.sh
```

The acceptance runner verified the complete backend lifecycle:

1. Initialized an isolated PostgreSQL environment.
2. Applied the complete Alembic migration chain.
3. Seeded platform roles, organisation identities and permissions.
4. Started the API with integration-only Redis state.
5. Passed liveness and dependency-aware readiness checks.
6. Authenticated an organisation administrator.
7. Submitted a three-applicant batch through the public endpoint.
8. Verified two completed scores and one controlled no-match result.
9. Verified 100% terminal job progress and correct per-item isolation.
10. Removed the temporary API and database resources.

The controlled no-match record demonstrates that an individual bureau outcome
is isolated and reported without interrupting successful applicants in the same
batch.

## Containerized isolated end-to-end acceptance

```bash
./scripts/run_handover_e2e.sh
```

The containerized runner built the dedicated integration image, started
PostgreSQL, Redis and the API through Docker Compose, applied migrations, seeded
RBAC/demo identities, passed health and readiness checks, and reproduced the
same two-success/one-controlled-failure batch result. It completed successfully
on 4 July 2026 and is included as a GitLab validation job.
