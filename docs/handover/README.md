# PaySwitch Backend Handover Pack

**Prepared:** 30 June 2026  
**Owner:** Backend Engineering  
**Status:** **Complete and ready for client handover**

## Executive position

The PaySwitch backend is delivered as a production-oriented, security-conscious
FastAPI platform with organisation isolation, platform and organisation RBAC,
credit-bureau integration, synchronous and batch scoring, monitoring dashboards,
training-data workflows, auditability, Azure service integrations, and
Kubernetes/Argo deployment assets.

The implementation has passed the supplied quality, typing, security,
dependency, coverage, migration, manifest and isolated end-to-end verification
gates. The same gates are encoded in GitLab CI for repeatable release assurance.

## Deliverables

| Deliverable | Purpose | Status |
|---|---|---|
| [Executive handover summary](executive-summary.md) | Client-facing overview of delivered capability and assurance | Complete |
| [Responsibility matrix](responsibility-matrix.md) | Ownership across Backend, Frontend, AI, Data and assurance teams | Complete |
| [Delivery acceptance checklist](delivery-acceptance-checklist.md) | Completed backend scope and coordinated project acceptance | Ready for coordination |
| [Backend Internal QA report](qa/backend-qa-report.md) | Verified functional and quality outcomes | Passed |
| [Verification evidence](qa/verification-evidence.md) | Reproducible commands and recorded results | Passed |
| [Backend code-review update](code-review/backend-code-review.md) | Architecture, maintainability and review conclusions | Approved for handover |
| [Backend DPIA technical contribution](privacy/backend-dpia-technical-input.md) | Privacy-by-design controls and processing context | Complete technical contribution |
| [Internal application security assessment](security/internal-vapt-report.md) | Internal VAPT methodology and verified result | Passed |
| [Security assurance register](security/security-assurance-register.md) | Consolidated security verification evidence | Complete |
| [Implemented security controls](security/backend-security-controls.md) | Preventive, detective and operational controls | Complete |

## Delivery and deployment material

- [Architecture overview](../architecture/overview.md)
- [Data-flow architecture](../architecture/data-flows.md)
- [Data model](../architecture/data-model.md)
- [Security architecture](../architecture/security.md)
- [Root deployment guide](../deployment/README.md)
- [Kubernetes deployment overview](../deployment/k8s-overview.md)
- [Kubernetes and Argo operator guide](../../infra/k8s/README.md)
- [GitLab CI pipeline](../../.gitlab-ci.yml)
- [Docker isolated E2E runner](../../scripts/run_handover_e2e.sh)
- [Local isolated E2E runner](../../scripts/run_handover_e2e_local.sh)

## Assurance model

The handover uses complementary assurance layers:

1. **Engineering verification** — automated tests, coverage, linting, typing,
   dependency audit, static security analysis and isolated E2E.
2. **Deployment verification** — image least privilege, Kustomize rendering,
   Argo PreSync migration control and readiness checks.
3. **Internal security assessment** — source, configuration, authentication,
   authorization and tenant-isolation review by the implementation team.
4. **Independent assurance** — the project coordinator will engage the agreed
   third-party provider for an independent VAPT in the client-authorized
   environment. This provides additional independent validation of the delivered controls.

## One-command verification

```bash
./scripts/verify_handover.sh
./scripts/run_handover_e2e_local.sh
```

Both commands are safe for a non-production verification environment and use
isolated resources.
