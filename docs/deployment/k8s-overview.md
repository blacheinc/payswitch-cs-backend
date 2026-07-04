# Kubernetes Deployment Overview

> Audience: engineering and platform teams integrating PaySwitch with Kubernetes, Argo CD and GitLab CI. This overview explains the deployment architecture, supplied assets and environment-level configuration choices.
>
> **The Bicep / Container Apps deployment in [`docs/deployment/README.md`](./README.md) is one valid target — k8s is another.** The application code is identical for both; only the deploy artefacts differ.

---

## TL;DR for the call

The app is already 12-factor-clean:

- **All config is env vars** (pydantic-settings) — k8s `Deployment.spec.template.spec.containers[].env` works directly.
- **Secrets resolve via Azure Key Vault with env-var fallback** ([`app/config/keyvault_source.py`](../../app/config/keyvault_source.py)). Either pipeline works:
  - Inject env vars from a k8s `Secret` (sourced from External Secrets Operator → Azure KV, or sealed-secrets, or plain `kubectl create secret`); OR
  - Inject only `AZURE_KEYVAULT_URL` + service-principal env vars and let the app fetch secrets from KV at boot.
- **Single container, no sidecars.** One image (`Dockerfile` at the repo root). Liveness uses `/health`; readiness checks Postgres and Redis through `/ready`.
- **In-process workers.** 5 batch-scoring workers + 3 Service Bus subscribers per pod. **Total parallelism = replicas × 5.** This matters for HPA tuning.
- **Stateless.** Postgres, Redis, Service Bus, and Blob Storage are all external. Pods can be killed/replaced without data loss.

Concrete artefacts in this repo:

```
infra/k8s/
├── base/                       ← canonical Kustomize base (everything reusable)
├── overlays/example/           ← shows how to layer an env-specific overlay
├── argocd-application.example.yaml
├── gitlab-ci.example.yml
└── README.md
```

These are production-oriented reference assets ready to adapt in the target cluster repository. They are portable across AKS and other conformant Kubernetes distributions; Azure Key Vault integration through External Secrets Operator remains optional.

---

## What changes vs. the Container Apps deployment

| Concern | Container Apps (Bicep) | Kubernetes |
|---|---|---|
| Compute | `Microsoft.App/containerApps` | `Deployment` + `Service` + `Ingress` |
| Autoscaling | KEDA-style HTTP concurrency baked in | `HorizontalPodAutoscaler` (CPU + custom metrics if available) |
| Ingress | ACA built-in | nginx-ingress / Traefik / Istio Gateway / cluster's own |
| Secrets | KV via SP env vars OR ACA secret refs | k8s `Secret` injected as env, ideally synced from KV via External Secrets Operator |
| Migrations | Entrypoint-controlled | Argo PreSync `Job` is the sole migration/bootstrap owner; API pods disable both |
| Image source | Pushed to ACR | Pushed to GitLab Container Registry (or ACR — image is the same) |
| GitOps | None (script-driven) | Argo CD watches a path in git; `kubectl apply -k` is what Argo runs |
| CI | Anything | GitLab CI builds image + bumps the manifests |

What stays exactly the same:

- The `Dockerfile`, the application code, every env var name.
- All FastAPI routes, all RBAC, all dashboard endpoints.
- The Phase-B Key Vault source and the env-fallback behaviour.
- The image entrypoint; Kubernetes disables its migration/bootstrap actions through ConfigMap flags.

---

## Four environment integration choices

These choices align the supplied application manifests with the target platform's established standards.

### 1. How do secrets get into the cluster?

| Option | When to pick |
|---|---|
| **External Secrets Operator → Azure Key Vault** | They already use ESO or similar. Cleanest fit because we already model secrets in Azure KV. Provides automatic refresh, version pinning, and a single source of truth. |
| **Plain k8s `Secret` populated by GitLab CI** | Suitable where ESO is not part of the platform standard. CI reads from the approved secret store and creates or updates the Secret; rotation is performed through a pipeline run. |
| **Sealed Secrets** | Suitable where Bitnami's controller is the established GitOps secret standard. Encrypted `.sealed.yaml` resources can be versioned without direct Key Vault synchronization. |
| **Use the app's KV-source path** | Inject only `AZURE_KEYVAULT_URL` + the SP credentials (3 env vars) as a small k8s `Secret`. The app fetches the remaining approved secrets from KV at boot. This minimizes in-cluster secret duplication; account for Key Vault connectivity in startup readiness. |

**My recommendation**: ESO if they have it; otherwise the "tiny SP-only Secret + app reads from KV" path because it preserves the Azure-native model and avoids re-implementing secret rotation in CI.

### 2. Where do migrations run?

| Option | When to pick |
|---|---|
| **Argo CD pre-sync `Job` running `alembic upgrade head`** | Recommended for production. Argo waits for the Job to succeed before rolling pods. Failed migrations block traffic from ever touching the new image. Visible in the Argo UI. |
| **Entrypoint migration** (optional alternative) | Suitable for single-replica local/dev use only; not enabled in the supplied Kubernetes base. |

The supplied Kubernetes base uses the production-safe Job pattern. API pods set
`RUN_DB_MIGRATIONS=false` and `RUN_BOOTSTRAP_ADMIN=false`; the PreSync Job runs
both exactly once before rollout.

### 3. How is the image built and shipped?

The natural pipeline is GitLab CI → GitLab Container Registry → Argo CD. Two GitOps patterns:

| Pattern | Detail |
|---|---|
| **Tag bumping in CI** | Pipeline pushes `image:<sha>` and writes that tag into `kustomization.yaml`'s `images` block via a follow-up commit/MR. Argo sees the manifest change and syncs. **Most controllable.** |
| **Argo CD Image Updater** | An add-on that watches the registry and bumps tags itself. No CI commit needed. **Less plumbing**, but newer / less battle-tested. |

We provide a CI example using pattern 1.

### 4. How many replicas, what HPA target?

Each pod runs:

- 1 FastAPI process (HTTP)
- 5 batch-scoring async workers
- 3 Service Bus subscribers

Keep `WORKER_COUNT` at or below approximately 10 per pod and scale horizontally for additional throughput. A suitable initial HPA policy is:

```yaml
minReplicas: 2          # so a deploy never drains traffic to zero
maxReplicas: 10
metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
```

Where Prometheus and a custom-metrics adapter are available, request concurrency per pod can complement CPU as a scaling signal.

---

## Platform integration configuration

The platform team supplies these environment-specific values and policies:

- **Ingress class** — nginx? traefik? Istio? We use a generic `Ingress`; they slot in their `IngressClassName`.
- **TLS certificates** — cert-manager + Let's Encrypt is standard; or they bring their own ACM/cluster cert.
- **Network policies** — restrict egress to Postgres / Redis / Service Bus / Key Vault / DE & Orchestrator function URLs.
- **PodDisruptionBudget** — `minAvailable: 1` so a node drain doesn't take all pods at once.
- **Resource limits** — Postgres connection budget vs. replica count × `database_pool_size`.
- **Observability stack** — they probably already have Prometheus/Grafana/Loki or ELK or Datadog. Our app emits structured stdout logs (post-Phase-B) and `RequestMetric` rows; nothing else needs to be plumbed.

---

## Common architecture questions

When the call gets technical, these answer the most likely questions:

> **"Does this run on managed identity?"**
> Azure data-plane services use connection strings as required by the project integration standard. Key Vault authenticates through a service principal (`client-id`, `client-secret`, `tenant-id`) because Key Vault does not expose a connection-string credential.

> **"Where do batch workers run?"**
> Workers run in-process inside each FastAPI pod and start through the lifespan hook. Each replica contributes five workers, making replica scaling the primary throughput control.

> **"How do migrations work?"**
> Argo runs `alembic upgrade head` and bootstrap through the supplied PreSync
> Job. API replicas explicitly disable both entrypoint actions, preventing a
> migration race during scale-up or rolling deploys.

> **"Service Bus auth?"**
> A Service Bus connection string is injected from the approved Kubernetes secret source. Rotation follows the platform's normal secret update and rolling-restart procedure; a least-privilege application authorization rule is recommended.

> **"Postgres connections?"**
> Each pod opens up to `database_pool_size + database_max_overflow = 30` connections by default. With 5 replicas that's 150 — adjust `max_connections` on the Postgres server accordingly, or front it with PgBouncer.

> **"Logging?"**
> Structured stdout — Loki / Fluent Bit / whatever ships container stdout to their log store works out of the box. No log shipper sidecar needed.

> **"Health checks?"**
> `GET /health` is the process liveness probe. `GET /ready` checks Postgres and
> Redis and is the readiness probe, so unavailable dependencies remove a pod
> from service without forcing a liveness restart loop.

---

## Pointing at the artefacts

**[`infra/k8s/README.md`](../../infra/k8s/README.md)** is the operator-facing entry point — tells them what each file is for, how to consume the base + overlays from Argo, and what the GitLab pipeline does.

If they want to see the full Argo CD wiring, point at **[`infra/k8s/argocd-application.example.yaml`](../../infra/k8s/argocd-application.example.yaml)**.

If they want to see how CI fits, point at the canonical **[`.gitlab-ci.yml`](../../.gitlab-ci.yml)**. The smaller **[`infra/k8s/gitlab-ci.example.yml`](../../infra/k8s/gitlab-ci.example.yml)** only demonstrates a separate GitOps tag-bump pattern.

The assets are complete integration references. The platform team applies its domain, registry, Argo project, secret source and resource sizing through the supplied overlay pattern.
