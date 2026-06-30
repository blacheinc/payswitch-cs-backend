# Backend Verification Evidence

**Executed:** 30 June 2026  
**Source baseline:** `3a0d27d` plus the handover working-tree changes  
**Migration head:** `f8a9b0c1d2e3`

## Repeatable handover gate

Command:

```bash
./scripts/verify_handover.sh
```

Recorded result:

| Check | Result |
|---|---|
| Ruff lint | Zero findings |
| Ruff format | 124 files correctly formatted |
| mypy | Zero issues across 112 source/test files |
| pytest | 88 passed |
| Overall coverage | 60.61%; required 60% |
| Critical-path coverage | 88.71%; required 80% |
| pip-audit | No known vulnerabilities |
| Bandit | Zero findings |
| Kustomize base/overlay | Rendered successfully |
| Argo PreSync assertion | Passed |
| Kubernetes non-root assertion | Passed |
| Integration Compose validation | Passed |

The local project package is skipped by `pip-audit` because it is not a PyPI
dependency; all resolved third-party packages are audited.

## Isolated E2E

Command:

```bash
./scripts/run_handover_e2e_local.sh
```

Recorded result:

- Temporary PostgreSQL initialized and all Alembic revisions applied.
- Minimal demo identities and RBAC seeded.
- `/ready` returned success.
- `admin@gcm.com.gh` authenticated.
- Three batch items accepted through `POST /v1/score/batch`.
- Terminal state: `completed=2`, `failed=1`, `pending=0`, `processing=0`.
- Failed item error code: `bureau_failed`.
- Health and readiness smoke checks passed after batch completion.
- API process and temporary database were removed by the script trap.

The Docker-equivalent command is:

```bash
./scripts/run_handover_e2e.sh
```

It is also wired into the supplied GitLab pipeline.
