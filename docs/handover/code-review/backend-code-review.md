# Backend Code-Review Update

**Review date:** 30 June 2026  
**Owner:** Backend Engineering  
**Decision:** **Ready for peer review and client non-production deployment**

## Resolved findings

| Finding | Resolution/evidence |
|---|---|
| Hardcoded test credentials | Removed; test scripts require environment variables |
| Vulnerable dependency resolution | Safe version floors; `pip-audit` reports zero known vulnerabilities |
| Root/dev-heavy runtime image | Multi-stage runtime-only image, UID/GID 999 |
| Undefined privileged-route variables | Corrected to authenticated context; Ruff has zero findings |
| Migration race across replicas | Argo PreSync Job is sole migration/bootstrap owner |
| ORM/API type inconsistencies | UUID/datetime and response boundaries corrected; mypy has zero errors |
| Lint backlog | Reduced from 333 to zero |
| Type backlog | Reduced from 106 to zero |
| Insufficient regression suite | Expanded from 28 to 88 tests |
| Overall coverage | Raised from 3% to 60.61%, enforced at 60% |
| Critical-path coverage | 88.71%, enforced at 80% |
| Static security findings | Bandit reports zero findings |
| Kubernetes pod hardening | Non-root, no privilege escalation, read-only root filesystem, dropped capabilities, RuntimeDefault seccomp |

## Positive implementation findings

- Central JWT/API-key authentication and role/permission enforcement.
- Tenant identity derived from authentication, not user-supplied filters.
- Explicit platform-admin cross-org permissions and audit records.
- Bcrypt, TOTP, cryptographically generated secrets and constant-time HMAC checks.
- Redis idempotency/rate limiting and bounded outbound HTTP calls.
- Production fail-fast configuration with Key Vault/environment secret sourcing.
- Long-lived Service Bus consumers with complete/abandon/dead-letter behavior.
- Deterministic, external-service-free batch E2E mode for CI acceptance.
- GitLab quality/test/security/package/deployment-validation stages.
- Argo-compatible Kustomize manifests and one-shot migration hook.

## Tracked operational decisions

These are deployment/operations items rather than source release blockers:

1. Rate limiting intentionally fails open; enforce a second limit at ingress,
   API Management or WAF and alert on Redis failure.
2. Pin the final Python base-image digest under the client's image refresh policy.
3. Rotate any credential that appeared in earlier Git history.
4. Run DAST and independent VAPT against an authorized staging deployment.
5. Complete the longer-term migration away from legacy role-label claims once
   every frontend consumer uses RBAC role/permission responses.

## Peer-review checklist

- [ ] Review the branch diff and migration topology.
- [ ] Confirm GitLab protected variables and registry permissions.
- [ ] Confirm Argo project, namespace and PreSync hook permissions.
- [ ] Execute `scripts/run_handover_e2e.sh` in CI/non-production.
- [ ] Attach registry scan, DAST and independent VAPT results to release approval.
