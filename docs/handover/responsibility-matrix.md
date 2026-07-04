# Project Handover Responsibility Matrix

This matrix translates the project coordinator's handover request into clear
ownership across the implementation and assurance teams.

| Deliverable | Primary owner | Contributors / reviewers | Expected output |
|---|---|---|---|
| Consolidated project QA report | QA lead / Project Coordinator | Backend, Frontend, AI and Data engineers | One project-level summary supported by each discipline's QA evidence |
| Backend Internal QA report | Backend Engineer | QA lead | API, RBAC, scoring, integration, deployment and automated verification evidence |
| Frontend Internal QA report | Frontend Engineer | QA lead, Backend Engineer | UI flows, accessibility, browser/device and API-integration evidence |
| AI Internal QA report | AI Engineer | Data Engineer, QA lead | Model evaluation, inference contracts, explainability and performance evidence |
| Data Internal QA report | Data Engineer | AI Engineer, QA lead | Pipeline, transformation, data quality, lineage and reconciliation evidence |
| Code-review updates | Each discipline lead | Peer reviewer from the team | Final architecture, maintainability and review outcome per codebase |
| Project DPIA | DPO / Privacy Lead | Backend, Frontend, AI, Data, Product and Security | Approved project DPIA using each team's technical contribution |
| Backend DPIA contribution | Backend Engineer | DPO, Security | Processing flows, RBAC, audit, encryption, retention interfaces and data categories |
| Frontend DPIA contribution | Frontend Engineer | DPO, Security | Browser storage, consent/notices, session handling and data presentation |
| AI DPIA contribution | AI Engineer | DPO, Data Engineer | Automated decisioning, fairness, explainability and human-review context |
| Data DPIA contribution | Data Engineer | DPO, AI Engineer | Data origin, lineage, minimisation, quality, residency and lifecycle context |
| Internal VAPT | Security/QA lead | All engineering disciplines | Coordinated internal source/configuration and deployed-environment assessment |
| Backend internal security assessment | Backend Engineer | Security/QA lead | Authentication, authorization, tenant isolation, dependency and deployment-control evidence |
| Independent VAPT | Independent third-party provider | Project Coordinator, Security, all engineers | Independent signed assessment and retest report |
| Security controls register | Security/QA lead | Backend, Frontend, AI and Data engineers | Consolidated control catalogue with implementation evidence |
| Backend security controls | Backend Engineer | Security/DevOps | API, identity, RBAC, audit, secrets, container, CI and Kubernetes controls |
| Deployment package | Backend Engineer / DevOps | Data and AI engineers for service dependencies | Deployment guide, manifests, pipeline, migration job and verification scripts |
| Final handover consolidation | Project Coordinator | All discipline owners | Indexed delivery pack, repository references and acceptance checklist |

## Backend package supplied

Backend Engineering has supplied all backend-owned rows above in
[`docs/handover`](./README.md). The package is structured so the project
coordinator can incorporate it directly into the consolidated delivery without
rewriting technical evidence.
