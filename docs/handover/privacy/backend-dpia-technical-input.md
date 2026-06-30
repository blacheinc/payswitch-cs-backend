# Backend Technical Input for DPIA

**Prepared:** 29 June 2026  
**Purpose:** engineering facts for the project/client DPO  
**Important:** this is not legal advice, a completed DPIA or approval to process

## 1. Processing overview

The backend receives identity and loan-application information, obtains credit
bureau data, invokes scoring/model services, applies hard-stop/business rules,
stores requests and decisions, and exposes results to authorized organisation
users and platform administrators. The flow includes automated decision support
and can produce approve, conditional approve, refer, decline, fraud hold or
error outcomes.

## 2. Data categories

| Category | Examples | Sensitivity |
|---|---|---|
| Identity | Name, date of birth, Ghana/national ID, account number | High |
| Contact | Telephone, email, residential address | High |
| Credit history | Accounts, arrears, judgements, defaults, payment history | Very high |
| Employment/financial | Employer details, income indicators, loan amount/purpose | Very high |
| Model outputs | Credit score, probability/risk tier, fraud flags, explanations | Very high |
| Operational metadata | Organisation, API key prefix, request ID, timestamps, latency | Moderate |
| Security/audit | Actor ID/email, IP address, user agent, action and resource | Moderate/high |
| Administrator data | Email, name, password hash, MFA secret/status | High |

Passwords and full API keys are not stored in plaintext. Test credentials were
removed from the current repository tree; historical values require rotation.

## 3. Data subjects

- Loan/credit applicants and existing borrowers.
- Organisation staff using the portal/API.
- Platform administrators and operational staff.
- Individuals represented in training or performance datasets.

## 4. Data flows and recipients

| Flow | Data sent | Recipient/system | Purpose |
|---|---|---|---|
| Client → Backend | Applicant/loan details | Backend API/PostgreSQL | Initiate and track scoring |
| Backend → XDS | Identity/contact match fields | XDS credit bureau | Match and retrieve bureau report |
| Backend → DE/AI inference | Bureau-derived/model feature payload | Azure Function/model service | Generate scoring outputs |
| Backend ↔ Service Bus | Request IDs, model/training event payloads | Azure Service Bus | Asynchronous orchestration |
| Backend → Blob Storage | Training/bulk files and model metadata | Azure Storage | Processing and artefact retention |
| Backend → Redis | Rate-limit/idempotency/queue records | Redis | Abuse control and batch processing |
| Backend → Email provider | Recipient email and transactional content | Configured email provider | Invitation/reset notifications |
| Backend → Logs | Actor/org/request metadata | PostgreSQL/log platform | Audit, monitoring and incident response |

See [data flows](../../architecture/data-flows.md) and
[data model](../../architecture/data-model.md) for diagrams.

## 5. Automated decision-making

The system performs profiling and automated scoring with potentially significant
credit consequences. Engineering has implemented decision labels, model metadata,
reason/explanation payloads, hard-stop reasons and a manual override route for
referred cases. The DPO/product owner must determine:

- whether decisions are solely automated or always reviewed by a human;
- the lawful basis and required notices/consent;
- how a data subject challenges or requests human review;
- which explanation is meaningful and safe to disclose;
- acceptable fairness, drift and false-decision thresholds.

## 6. Technical safeguards

- Role-based access and organisation isolation.
- Explicit, audited platform-admin cross-org permissions.
- Bcrypt password hashing, TOTP MFA and short-lived JWT access tokens.
- Hashed API keys and HMAC-signed webhooks.
- Validation, rate limiting and idempotency controls.
- Environment/Key Vault secret management and production fail-fast validation.
- TLS expected for public ingress and external service connections.
- Request/audit logging and deployment monitoring.
- Non-root production container and dependency/security scanning.

## 7. Privacy risks and proposed treatment

| Risk | Impact | Existing control | Further decision/action |
|---|---|---|---|
| Cross-organisation disclosure | Severe confidentiality breach | Auth-derived org filters, RBAC, audit | Independent BOLA/IDOR testing |
| Excessive collection | Unnecessary exposure | Schemas constrain known fields | DPO approve field-by-field necessity |
| Incorrect bureau match | Wrong adverse decision | Match status, identity confidence, refer flow | Define human-review threshold and correction route |
| Model bias/discrimination | Unfair access to credit | Monitoring/fairness interfaces | AI/DPO define protected groups and acceptance thresholds |
| Unexplainable adverse action | Regulatory/customer harm | Reason codes/model metadata | Approve customer-facing explanation policy |
| Retention beyond necessity | Increased breach impact | Deletion cascades in selected relations | Define and automate retention schedule |
| Admin misuse | Broad visibility across tenants | Separate permissions and audit logs | Periodic access review and alerting |
| Secret compromise | External-system/data access | Vault/env controls, rotation procedures | Rotate historic secrets; establish rotation cadence |
| Third-country/subprocessor transfer | Regulatory breach | Configurable endpoints/providers | Client DPO inventory regions and contracts |
| Log overcollection | IP/identity exposure | Logs avoid response bodies | Set retention and restrict monitoring access |

## 8. Decisions still required from the data controller/DPO

- Controller/processor roles for PaySwitch, client institutions, XDS, hosting,
  email and AI providers.
- Lawful basis for bureau retrieval, profiling and automated decisions.
- Retention period for requests, bureau payloads, training files, audit/API logs,
  model outputs, inactive accounts and backups.
- Data-residency and international-transfer requirements.
- Data-subject access, correction, deletion, objection and human-review workflow.
- Breach-notification contacts and timeframes.
- Whether raw bureau/training data may be used to retrain models.
- Minimum age and special-category-data handling requirements.

## 9. Known functional gaps relevant to privacy

- No complete data-subject export/delete workflow has been verified.
- Retention is not centrally encoded as an automated policy.
- Live E2E privacy/isolation checks require a functioning staging database.
- Final AI fairness/explainability assessment belongs to the AI engineer/DPO.

