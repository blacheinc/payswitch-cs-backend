# Backend Technical Contribution to the DPIA

- **Prepared:** 30 June 2026
- **Contributor:** Backend Engineering
- **Status:** **Complete technical contribution for project-level DPIA review**

## Processing context

The backend supports credit application processing by receiving applicant and
loan information, retrieving authorized credit-bureau information, applying
deterministic eligibility rules, coordinating model inference, recording the
result and exposing it to authorized organisation and platform users.

The implementation supports approve, conditional approve, refer, decline,
fraud-hold and error outcomes. Manual review and override capabilities are
available for referred applications, and decision reasons/model metadata are
retained for operational explainability.

## Data categories and purpose

| Category | Representative data | Processing purpose |
|---|---|---|
| Applicant identity | Name, date of birth, national ID, account reference | Bureau matching and application identification |
| Contact | Telephone and email | Match support and transactional communication |
| Credit history | Accounts, arrears, judgements, defaults, payment history | Credit assessment and eligibility controls |
| Financial/application | Requested amount, tenure, purpose, income indicators | Affordability and lending assessment |
| Model outputs | Score, probability, risk tier, fraud indicator, reason codes | Decision support and review |
| Operational metadata | Organisation, request ID, timestamps, latency | Traceability, monitoring and service operation |
| Security/audit | Actor, action, resource, IP and user agent | Access accountability and incident investigation |
| Workforce identity | User name/email, role, password hash and MFA state | Secure platform administration and use |

The backend stores password hashes rather than plaintext passwords and stores
API-key hashes rather than recoverable full keys.

## Data-flow summary

| Flow | Data recipient | Purpose and safeguard |
|---|---|---|
| Client application → Backend | Backend API/PostgreSQL | Validated application submission over TLS |
| Backend → XDS | Credit bureau | Authorized identity matching and report retrieval |
| Backend → DE/AI services | Approved bureau/model payload | Model inference through configured authenticated endpoints |
| Backend ↔ Service Bus | Azure Service Bus | Durable asynchronous event coordination |
| Backend → Blob Storage | Azure Storage | Controlled training and model-artifact storage |
| Backend → Redis | Redis | Short-lived idempotency, rate-limit and queue state |
| Backend → Email provider | Configured transactional provider | Invitations and account-recovery messages |
| Backend → Audit/monitoring | PostgreSQL and client logging platform | Security and operational traceability |

Detailed diagrams are available in [data flows](../../architecture/data-flows.md)
and [data model](../../architecture/data-model.md).

## Privacy-by-design controls implemented

- Organisation-scoped access derived from authenticated identity.
- Dedicated permissions and audit records for platform cross-organisation access.
- Role-based access at endpoint and dashboard level.
- TOTP MFA for platform administration and short-lived JWT access tokens.
- Hashed passwords/API keys and HMAC-signed webhook messages.
- Pydantic schemas that constrain accepted fields, lengths, ranges and identifiers.
- Purpose-specific service integrations configured by the deployment operator.
- Request IDs and audit events without routine response-body capture.
- Explicit decision, reason, model and override records supporting reviewability.
- Dependency-aware readiness and structured operational monitoring.
- Key Vault/environment secret sourcing and production configuration validation.
- Non-root, least-privilege container and Kubernetes execution.

## Automated decision-support safeguards

The backend records the information required to support responsible decision
governance:

- decision and condition labels;
- hard-stop rule codes and reasons;
- model version and scoring timestamp;
- probability/risk and data-quality measures;
- feature contribution/reason-code payloads;
- manual override identity, reason and timestamp;
- subsequent outcome and loan-performance records.

These capabilities support the project's approved customer-notice, human-review,
fairness and explainability processes.

## Privacy assurance matrix

| Privacy objective | Backend implementation | Project governance use |
|---|---|---|
| Access limitation | RBAC, tenant scoping and audited privileged access | Role approval and periodic access review |
| Data minimisation | Typed request contracts and purpose-specific integrations | Field-purpose review by Product/DPO |
| Accuracy | Bureau status, identity confidence, validation and correction-ready records | Applicant correction and human-review procedure |
| Transparency | Decision reasons, model metadata and timestamps | Customer-facing notice/explanation design |
| Accountability | Audit logs, request IDs and override records | Compliance review and incident evidence |
| Security | Encryption-in-transit expectations, hashing, MFA, secrets and least privilege | Infrastructure and security operating procedures |
| Lifecycle governance | Timestamped domain records and documented data model | DPO-approved retention schedule and automated operations |
| Resilience | Idempotency, queue isolation, readiness and monitoring | Business-continuity and service-management processes |

## Inputs for project-level approval

The DPO/Product/Security review can use this technical contribution to confirm:

- controller/processor roles and approved subprocessors;
- lawful basis and customer notices for bureau retrieval and profiling;
- the approved retention schedule for each data category;
- data residency and transfer requirements;
- data-subject access, correction, objection and human-review procedures;
- the approved use of training and performance data;
- fairness/explainability thresholds supplied by the AI workstream.

Backend Engineering will support the project team in translating the approved
governance decisions into environment configuration and operating procedures.
