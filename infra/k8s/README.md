# Kubernetes Deployment

> Companion to [`docs/deployment/k8s-overview.md`](../../docs/deployment/k8s-overview.md), which defines the Kubernetes integration architecture. **This file is the operator-facing reference for the manifests.**
>
> The Bicep / Container Apps deploy in [`docs/deployment/README.md`](../../docs/deployment/README.md) is an alternative, not a prerequisite — the same image works for both targets.

---

## Layout

```
infra/k8s/
├── base/                       ← canonical manifests (do not edit per env)
│   ├── namespace.yaml
│   ├── configmap.yaml          ← non-secret runtime config
│   ├── external-secret.yaml    ← ESO sync from Azure Key Vault (recommended)
│   ├── secret.example.yaml     ← alternative: manage Secret out-of-band
│   ├── deployment.yaml         ← the API pod
│   ├── service.yaml
│   ├── ingress.yaml            ← nginx-ingress defaults; swap as needed
│   ├── hpa.yaml                ← CPU/memory-driven autoscaling
│   ├── job-migrate.yaml        ← Argo PreSync hook for alembic upgrade
│   └── kustomization.yaml
├── overlays/
│   └── example/
│       └── kustomization.yaml  ← fork per env (prod, staging, …)
├── argocd-application.example.yaml
├── gitlab-ci.example.yml        ← minimal reference; root .gitlab-ci.yml is canonical
└── README.md (this file)
```

Argo CD points at an overlay path; the overlay layers env-specific
patches over the immutable base.  No Helm — Kustomize keeps the
manifests inspectable in plain `kubectl diff` and `kubectl apply -k`.

---

## Quick start (operator)

```bash
# 1. Fork the example overlay for your environment
cp -r infra/k8s/overlays/example infra/k8s/overlays/prod
# ...edit overlays/prod/kustomization.yaml: namespace, image tag,
# Ingress host, ConfigMap values

# 2. Decide on a secrets path:
#    a. ESO + Azure KV (recommended) — keep external-secret.yaml in
#       base/kustomization.yaml; install ESO + create a SecretStore
#       named `azure-kv` in the namespace.
#    b. Plain Secret — flip the comments in base/kustomization.yaml to
#       use secret.example.yaml instead, and populate via SealedSecrets,
#       SOPS, or `kubectl create secret`.

# 3. Dry-run the rendered manifests
kubectl kustomize infra/k8s/overlays/prod | less

# 4. Apply once by hand to verify (or skip and let Argo do it)
kubectl apply -k infra/k8s/overlays/prod

# 5. Wire Argo CD
kubectl apply -f infra/k8s/argocd-application.example.yaml
# (after editing repoURL, path, and destination)

# 6. CI builds the image and bumps the tag in your overlay; Argo syncs.
```

---

## Decision matrix — what to override per environment

| Concern | Override location |
|---|---|
| Namespace | `overlays/<env>/kustomization.yaml` → `namespace:` |
| Image tag | `overlays/<env>/kustomization.yaml` → `images[0].newTag` (or let CI rewrite it) |
| Ingress host + TLS secret | `overlays/<env>/kustomization.yaml` → `patches:` block |
| Public URLs in ConfigMap | `overlays/<env>/kustomization.yaml` → `configMapGenerator` block |
| Secret values | NEVER in git — use ESO, SealedSecrets, SOPS, or out-of-band |
| HPA min/max replicas | Patch the HPA in the overlay |
| Resource limits | Patch the Deployment in the overlay |

---

## Secrets — pick one path

### Path A: External Secrets Operator (recommended if available)

1. Install ESO in the cluster:
   ```bash
   helm install external-secrets external-secrets/external-secrets \
     -n external-secrets --create-namespace
   ```
2. Create a `SecretStore` (or `ClusterSecretStore`) named `azure-kv`
   pointing at the relevant Azure Key Vault, authenticated via a small
   bootstrap Secret holding the SP's client-id + client-secret.
   ([ESO Azure KV docs](https://external-secrets.io/latest/provider/azure-key-vault/).)
3. The committed [`base/external-secret.yaml`](./base/external-secret.yaml)
   pulls the secrets from KV into a `Secret` named `payswitch-secrets`
   that the Deployment mounts via `envFrom`.

Pros: rotation in KV automatically syncs to the cluster every
`refreshInterval`. Single source of truth.

### Path B: Plain Secret managed out-of-band

If ESO isn't available:

1. Open [`base/kustomization.yaml`](./base/kustomization.yaml) and flip
   the comments — comment out `external-secret.yaml`, uncomment
   `secret.example.yaml` (or remove it from the resources list and
   create the Secret a different way).
2. Create the `payswitch-secrets` Secret via one of:
   - **SealedSecrets** (Bitnami): commit `payswitch-secrets.sealed.yaml`
     to git; the controller decrypts in-cluster.
   - **SOPS + Argo plugin** (`kustomize-sops`): commit a SOPS-encrypted
     Secret; Argo decrypts at sync time.
   - **GitLab CI step**: `kubectl create secret generic payswitch-secrets
     --from-literal=...` in a deploy job.

The Deployment is identical regardless of which path produced the Secret.

### Path C: KV-driven (smallest in-cluster surface)

If you want the cluster to know almost nothing about the app's secrets:

1. Set only `AZURE_KEYVAULT_URL` in the ConfigMap.
2. Put `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` in a
   minimal Secret (3 keys total).
3. The app's Phase-B Key Vault source fetches every other secret at
   boot and caches them in-process.

Pros: rotation requires only a KV update + pod restart. Cluster never
holds the actual secret material.

Cons: slower cold start; KV becomes a hard runtime dependency for boot.

---

## Migrations

The supplied base uses the **Argo CD PreSync Job** pattern:

- [`base/job-migrate.yaml`](./base/job-migrate.yaml) is annotated with
  `argocd.argoproj.io/hook: PreSync`. Argo waits for the Job to succeed
  before applying the new Deployment.
- A failed migration blocks the sync — the new image never serves
  traffic.
- `RUN_DB_MIGRATIONS=false` and `RUN_BOOTSTRAP_ADMIN=false` on API pods
  prevent replica startup races.
- The Job's explicit command is the sole migration/bootstrap owner.

To disable the Job entirely (e.g. dev), remove it from
`base/kustomization.yaml`'s `resources` list.

---

## Health checks + scaling

| Knob | Default | Where |
|---|---|---|
| Liveness | `GET /health` every 30 s | `base/deployment.yaml` |
| Readiness | `GET /ready` every 10 s (Postgres + Redis) | `base/deployment.yaml` |
| Startup probe grace | 5 min (allows alembic on slow DBs) | `base/deployment.yaml` |
| HPA replicas | min 2, max 10 | `base/hpa.yaml` |
| HPA target | 70 % CPU, 80 % memory | `base/hpa.yaml` |
| PDB | `minAvailable: 1` | `base/deployment.yaml` |
| Graceful shutdown | 60 s | `base/deployment.yaml` |

The 60-second grace period lets in-flight scoring requests finish and Service
Bus subscribers acknowledge their messages before termination. Validate any
environment-specific adjustment with the isolated E2E suite.

---

## CI

The repository-root [`.gitlab-ci.yml`](../../.gitlab-ci.yml) is the canonical
handover pipeline. It runs:

1. **quality** — Ruff formatting/lint and mypy.
2. **test** — 60% overall and 80% critical-path coverage gates.
3. **security** — pip-audit and Bandit.
4. **package** — non-root container build and inspection.
5. **deploy-validate** — Kustomize/Argo policy checks and isolated E2E.

[`gitlab-ci.example.yml`](./gitlab-ci.example.yml) remains a smaller tag-bump
reference if the client separates application CI from its GitOps repository.

Required CI/CD variables (Project Settings → CI/CD → Variables):

| Variable | Purpose |
|---|---|
| `CI_REGISTRY_*` | Built-in — GitLab provides them |
| `GITOPS_DEPLOY_TOKEN` | Project access token with `write_repository` on the GitOps repo |

Adjust the example to match the team's tagging convention (semver tags
on releases vs. SHA tags on every main commit, etc.).

---

## Observability

The app emits structured stdout logs that integrate directly with cluster log
collectors such as Loki/Promtail, Fluent Bit/Elasticsearch or Datadog.

Application monitoring is provided by the platform monitoring endpoints,
`RequestMetric` persistence and health/readiness probes. Clusters that
standardize on Prometheus can add HTTP-level instrumentation and a
`ServiceMonitor` in their environment overlay without changing the deployment
contract.

---

## Troubleshooting

| Symptom | Likely cause | Where to look |
|---|---|---|
| Pods CrashLoopBackOff with `validate_production` errors | Missing required env var | `kubectl logs <pod>` — error lists the missing fields |
| Pods stuck `ContainerCreating` | Secret not yet synced by ESO, or ImagePullBackOff | `kubectl describe pod <pod>` |
| Argo App shows `OutOfSync` even after CI ran | Tag bump didn't push to the manifests repo, or Argo's polling interval hasn't elapsed | Check the GitOps repo HEAD; force `argocd app sync` |
| 502 from Ingress | Pod readiness probe failing — DB or KV not reachable from inside the cluster | `kubectl exec` into the pod and curl the dependency directly |
| Migration Job hangs | DB unreachable, or a long-running backfill | `kubectl logs job/payswitch-migrate -f` |

---

## Environment-owned configuration

These values are intentionally supplied by the target platform because they
depend on cluster standards and capacity:

- **Choice of Ingress controller** (we use `ingressClassName: nginx`; swap as needed).
- **TLS issuance** (cert-manager + Let's Encrypt is the assumed default; bring your own if not).
- **Network policies** restricting egress to Postgres / Redis / Service Bus / KV / DE-orchestrator URLs.
- **Postgres connection budget** vs. replica count × `database_pool_size` (default 30 per pod).
- **PgBouncer** in front of Postgres if connections become a bottleneck.
- **Metrics export integration** for the platform's selected observability stack.
