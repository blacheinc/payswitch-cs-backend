# Kubernetes Deployment Overview

> Audience: backend engineer briefing a client whose platform runs on Kubernetes with Argo CD and GitLab CI. The goal of this doc isn't a step-by-step deploy guide — it's a talking-points sheet so you can speak confidently about how this app fits into their stack and what decisions they own.
>
> **The Bicep / Container Apps deployment in [`docs/deployment/README.md`](./README.md) is one valid target — k8s is another.** The application code is identical for both; only the deploy artefacts differ.

---

## TL;DR for the call

The app is already 12-factor-clean:

- **All config is env vars** (pydantic-settings) — k8s `Deployment.spec.template.spec.containers[].env` works directly.
- **Secrets resolve via Azure Key Vault with env-var fallback** ([`app/config/keyvault_source.py`](../../app/config/keyvault_source.py)). Either pipeline works:
  - Inject env vars from a k8s `Secret` (sourced from External Secrets Operator → Azure KV, or sealed-secrets, or plain `kubectl create secret`); OR
  - Inject only `AZURE_KEYVAULT_URL` + service-principal env vars and let the app fetch secrets from KV at boot.
- **Single container, no sidecars.** One image (`Dockerfile` at the repo root). Liveness + readiness probes already exist on `/health`.
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

These are working starting points the client can fork into their cluster repo. They're not opinionated about cluster choice (AKS, EKS, GKE, on-prem) — the only Azure-specific bit is the External Secrets Operator integration with Key Vault, and even that is optional.

---

## What changes vs. the Container Apps deployment

| Concern | Container Apps (Bicep) | Kubernetes |
|---|---|---|
| Compute | `Microsoft.App/containerApps` | `Deployment` + `Service` + `Ingress` |
| Autoscaling | KEDA-style HTTP concurrency baked in | `HorizontalPodAutoscaler` (CPU + custom metrics if available) |
| Ingress | ACA built-in | nginx-ingress / Traefik / Istio Gateway / cluster's own |
| Secrets | KV via SP env vars OR ACA secret refs | k8s `Secret` injected as env, ideally synced from KV via External Secrets Operator |
| Migrations | Run in `entrypoint.sh` on every container start | Run as a pre-sync `Job` (Argo Sync Hook) — gives GitOps a checkable success/failure |
| Image source | Pushed to ACR | Pushed to GitLab Container Registry (or ACR — image is the same) |
| GitOps | None (script-driven) | Argo CD watches a path in git; `kubectl apply -k` is what Argo runs |
| CI | Anything | GitLab CI builds image + bumps the manifests |

What stays exactly the same:

- The `Dockerfile`, the application code, every env var name.
- All FastAPI routes, all RBAC, all dashboard endpoints.
- The Phase-B Key Vault source and the env-fallback behaviour.
- The `entrypoint.sh` migration step (still works fine; just optional in k8s if you prefer the Job pattern).

---

## The four design choices the client owns

These are the questions you should walk into the call ready to discuss. They're decisions about *their* infrastructure, not ours — but having a recommendation for each shows we've thought about it.

### 1. How do secrets get into the cluster?

| Option | When to pick |
|---|---|
| **External Secrets Operator → Azure Key Vault** | They already use ESO or similar. Cleanest fit because we already model secrets in Azure KV. Provides automatic refresh, version pinning, and a single source of truth. |
| **Plain k8s `Secret` populated by GitLab CI** | They don't have ESO and don't want to install it. CI reads from KV (or wherever) at deploy time and creates/updates the Secret. Simple, works, but ages poorly — secret rotation requires a pipeline run. |
| **Sealed Secrets** | They do GitOps for secrets and have Bitnami's controller. We'd commit `.sealed.yaml` files to git. Doesn't talk to KV directly. |
| **Stick with the app's KV-source path** | Inject only `AZURE_KEYVAULT_URL` + the SP creds (3 env vars) as a small k8s `Secret`. App fetches everything else from KV at boot. **Lowest k8s surface area** — they don't have to mirror every secret in cluster. Trade-off: slower cold start, KV becomes a runtime dependency for boot. |

**My recommendation**: ESO if they have it; otherwise the "tiny SP-only Secret + app reads from KV" path because it preserves the Azure-native model and avoids re-implementing secret rotation in CI.

### 2. Where do migrations run?

| Option | When to pick |
|---|---|
| **Argo CD pre-sync `Job` running `alembic upgrade head`** | Recommended for production. Argo waits for the Job to succeed before rolling pods. Failed migrations block traffic from ever touching the new image. Visible in the Argo UI. |
| **Keep migrations in `entrypoint.sh`** (the existing path) | Fine for dev and staging. Migration runs every pod start; the Deployment's readiness probe gates traffic. Simpler — no Job manifest needed. |

We provide both. Recommend the Job for prod, document the entrypoint behaviour for everyone else.

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

**Don't crank `WORKER_COUNT` per pod beyond ~10** — Python's GIL caps useful concurrency. **Scale horizontally instead.** A reasonable starting HPA:

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

If they have Prometheus + the custom-metrics adapter, request-concurrency-per-pod is a better signal than CPU. Document it; let them choose.

---

## Things the client should configure (their side)

Out of scope for our manifests, but worth raising:

- **Ingress class** — nginx? traefik? Istio? We use a generic `Ingress`; they slot in their `IngressClassName`.
- **TLS certificates** — cert-manager + Let's Encrypt is standard; or they bring their own ACM/cluster cert.
- **Network policies** — restrict egress to Postgres / Redis / Service Bus / Key Vault / DE & Orchestrator function URLs.
- **PodDisruptionBudget** — `minAvailable: 1` so a node drain doesn't take all pods at once.
- **Resource limits** — Postgres connection budget vs. replica count × `database_pool_size`.
- **Observability stack** — they probably already have Prometheus/Grafana/Loki or ELK or Datadog. Our app emits structured stdout logs (post-Phase-B) and `RequestMetric` rows; nothing else needs to be plumbed.

---

## Talking-points cheatsheet

When the call gets technical, these answer the most likely questions:

> **"Does this run on managed identity?"**
> No — connection-string style everywhere by design (per the project directive). Key Vault uses a service principal (client-id + secret + tenant), which is the closest thing KV offers to a connection string. Easy to swap for managed identity later if they want, since the SDK is the same.

> **"Where do batch workers run?"**
> In-process inside the FastAPI pod, started by the lifespan hook. Scaling = scale replicas. Each replica = 5 workers. No separate worker Deployment needed. We can split them out into a Job/CronJob later if their concurrency needs outgrow that model.

> **"How do migrations work?"**
> `alembic upgrade head` runs on every pod boot via the existing `entrypoint.sh`. For production they'll likely want this as a pre-sync Argo Job instead — we provide that manifest. Both patterns coexist; the Job runs first, the entrypoint becomes a no-op (the migration is already at head).

> **"Service Bus auth?"**
> Connection string in the `Secret`. No SAS rotation in cluster yet — set the long-lived "RootManageSharedAccessKey" connection string and rotate via standard k8s secret update.

> **"Postgres connections?"**
> Each pod opens up to `database_pool_size + database_max_overflow = 30` connections by default. With 5 replicas that's 150 — adjust `max_connections` on the Postgres server accordingly, or front it with PgBouncer.

> **"Logging?"**
> Structured stdout — Loki / Fluent Bit / whatever ships container stdout to their log store works out of the box. No log shipper sidecar needed.

> **"Health checks?"**
> `GET /health` returns `200 {"status": "ok", ...}`. Used by the existing liveness + readiness probes in our manifests. Returns 200 only when the lifespan startup completed (DB seed succeeded).

---

## Pointing at the artefacts

**[`infra/k8s/README.md`](../../infra/k8s/README.md)** is the operator-facing entry point — tells them what each file is for, how to consume the base + overlays from Argo, and what the GitLab pipeline does.

If they want to see the full Argo CD wiring, point at **[`infra/k8s/argocd-application.example.yaml`](../../infra/k8s/argocd-application.example.yaml)**.

If they want to see how CI fits, point at **[`infra/k8s/gitlab-ci.example.yml`](../../infra/k8s/gitlab-ci.example.yml)**.

These are starting points, not finished products — they'll fork them into their own cluster repo, swap in their domain / registry / Argo project, and tune the resource limits to their cluster shape.
