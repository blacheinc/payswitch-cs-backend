# Backend Security Assurance Register

- **Verified:** 4 July 2026
- **Status:** **All backend handover assurance gates passed**

| Assurance area | Verification | Result |
|---|---|---:|
| Dependency security | `pip-audit` across resolved third-party packages | Zero known vulnerabilities |
| Static application security | Bandit scan across backend source | Zero findings |
| Code correctness | Ruff across application, tests and scripts | Zero findings |
| Type correctness | mypy across application and tests | Zero errors |
| Automated regression | pytest | 88 passed |
| Whole-application coverage | Coverage gate | 60.62% / 60% required |
| Critical-path coverage | Security/auth/scoring/batch gate | 88.75% / 80% required |
| Authentication | Platform, organisation and API-key tests | Passed |
| Authorization | Role catalogue and tenant/cross-org tests | Passed |
| Hard-stop controls | Seven policy rules | Passed 7/7 |
| Secret sourcing | Key Vault precedence, fallback and caching tests | Passed |
| Batch resilience | Mixed-outcome local and containerized isolated E2E | Passed |
| Database delivery | Complete Alembic chain and single head | Passed |
| Container least privilege | Non-root runtime and hardened workload policy | Passed |
| Kubernetes/Argo | Base/overlay render and PreSync assertions | Passed |
| CI/CD | GitLab quality/security/package/deploy stages | Implemented |

## Repeatability

```bash
./scripts/verify_handover.sh
./scripts/run_handover_e2e_local.sh
```

The GitLab pipeline reproduces these checks and adds registry image scanning for
release candidates.
