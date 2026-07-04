# PaySwitch Backend Handover Document Pack

- **Package date:** 4 July 2026
- **Prepared by:** Backend Engineering
- **Scope:** PaySwitch Credit Scoring Backend
- **Classification:** Client handover

## Purpose

This pack consolidates the principal backend-engineering documents supporting
the PaySwitch project handover. It records the delivered backend architecture,
quality evidence, code-review outcome, privacy contribution, security controls
and internal application-security assessment.

## Included documents

| Document | Purpose |
|---|---|
| Executive Handover Summary | Concise delivery and assurance position |
| Responsibility Matrix | Ownership across Backend, Frontend, AI, Data and assurance teams |
| Delivery Acceptance Checklist | Completed backend scope and coordinated project actions |
| Backend Internal QA Report | Functional, quality and integration verification |
| Backend Verification Evidence | Reproducible checks and recorded results |
| Backend Code-Review Update | Architecture, maintainability and review conclusion |
| Backend DPIA Technical Contribution | Backend privacy-by-design controls and processing context |
| Internal Backend Application Security Assessment | Internal source, configuration and isolated-environment security assurance |
| Backend Security Controls Implemented | Preventive, detective and operational backend controls |
| Backend Security Assurance Register | Consolidated security verification evidence |
| Architecture Overview | Component topology and integration boundaries |
| Data Flows | Scoring, batch-processing and RBAC flows |
| Data Model | Persistence domains and lifecycle design |
| Security Architecture | Authentication, authorization, secrets and transport controls |

## Scope boundary

Backend Engineering has supplied deployment reference assets for Azure,
Kubernetes, Argo CD and GitLab CI to help the client's platform team integrate
the service. Production deployment execution, environment-specific platform
configuration and production acceptance remain owned by the client.

The project-level DPIA approval is owned by the designated DPO/Privacy Lead.
The independent VAPT is owned by the separately appointed third-party provider.
Those external approvals are intentionally not represented as completed by
Backend Engineering.

## Current backend assurance

- Automated test suite: 88 passed.
- Whole-application coverage: 60.62% against a 60% gate.
- Security/business-critical coverage: 88.75% against an 80% gate.
- Ruff, mypy, Bandit and third-party dependency audit: passed.
- Fresh-database migration and containerized isolated batch E2E: passed.
- Kubernetes/Argo reference-manifest validation: passed.
- One Alembic migration head: `a9b0c1d2e3f4`.
