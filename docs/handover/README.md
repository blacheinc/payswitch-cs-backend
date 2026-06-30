# Backend Handover Assurance Pack

**Prepared:** 30 June 2026  
**Owner:** Backend Engineering  
**Baseline:** working tree based on commit `3a0d27d`

This directory contains the backend engineer's contribution to the final
project handover. It deliberately separates verified evidence, open technical
debt, privacy input, and third-party work. An internal review is not represented
as an independent penetration test or legal approval.

## Document index

| Document | Purpose | Status |
|---|---|---|
| [Backend QA report](qa/backend-qa-report.md) | Functional, static, dependency, coverage and deployment checks | Pass for backend handover |
| [Verification evidence](qa/verification-evidence.md) | Exact final gate and isolated E2E results | Passed |
| [Backend code-review update](code-review/backend-code-review.md) | Resolved findings and peer-review checklist | Ready for peer review |
| [Internal VAPT report](security/internal-vapt-report.md) | Internal source/configuration security assessment | Internal pass; external testing pending |
| [Vulnerability remediation log](security/vulnerability-remediation-log.md) | Finding owner, treatment and evidence | Source findings closed; external actions tracked |
| [Backend security controls](security/backend-security-controls.md) | Implemented preventive, detective and operational controls | Ready for consolidation |
| [Backend DPIA technical input](privacy/backend-dpia-technical-input.md) | Data inventory, flows, risks and decisions needed from DPO/client | Technical input only |

## Related implementation material

- [Architecture overview](../architecture/overview.md)
- [Data flows](../architecture/data-flows.md)
- [Data model](../architecture/data-model.md)
- [Security architecture](../architecture/security.md)
- [Root deployment guide](../deployment/README.md)
- [Kubernetes deployment notes](../deployment/k8s-overview.md)
- [Kubernetes manifests](../../infra/k8s/README.md)
- [GitLab handover pipeline](../../.gitlab-ci.yml)
- [Docker isolated E2E](../../scripts/run_handover_e2e.sh)
- [Local isolated E2E](../../scripts/run_handover_e2e_local.sh)

## Items requiring another owner

1. The client DPO/legal owner must complete and approve the DPIA, lawful basis,
   retention schedule, international-transfer assessment and data-subject
   request process.
2. A third party must conduct and sign the independent VAPT report.
3. DevOps/client platform engineering must run DAST and infrastructure scans in
   a representative deployed environment.
4. Any credential previously committed to Git history must be rotated; removal
   from the current tree does not invalidate an exposed secret.
