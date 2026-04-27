# Frontend Integration Guide — Admin Cross-Org Reads

> **Companions**:
> [`fe_dashboard_integration_guide.md`](./fe_dashboard_integration_guide.md) (org-side dashboard endpoints) ·
> [`fe_admin_rbac_integration_guide.md`](./fe_admin_rbac_integration_guide.md) (platform admin RBAC) ·
> [`fe_monitoring_integration_guide.md`](./fe_monitoring_integration_guide.md) (admin monitoring tabs)

Platform admins can now drill into any organisation's scoring activity through the **same endpoints** the org dashboard already uses, plus one new endpoint built for the admin overview tile. This guide documents the contract and the FE integration patterns.

---

## 1. The shape of the change

Two patterns, applied consistently:

1. **Existing org endpoints accept an `organization_id` query param** when the caller is an admin holding the matching `admin.*.read` permission. Org users who pass it for their own org get the same result; org users who pass it for any other org get **403**.
2. **One new admin-only endpoint** (`GET /admin/score-requests/stats`) returns the same response shape as the org-side stats endpoint, scoped either platform-wide (no `organization_id`) or to a single org. Per-org tables are built FE-side by paginating `/admin/organizations` and calling this endpoint per visible row — see §5.

Mutations (override / outcome / performance / batch cancel / batch create) stay strictly org-scoped — see §6.

---

## 2. Permissions

Two new platform permissions added to the catalogue:

| Code | Default holders | Purpose |
|---|---|---|
| `admin.score_requests.read` | `SUPER_ADMIN`, `RISK_ANALYST`, `COMPLIANCE_OFFICER` | View any organisation's score requests + stats |
| `admin.batch_scoring.read` | `SUPER_ADMIN`, `RISK_ANALYST`, `COMPLIANCE_OFFICER` | View any organisation's batch jobs + per-item results |

`MODEL_OPS_ENGINEER` and `INFRASTRUCTURE_ENGINEER` deliberately do **not** have these grants — they don't need to see individual decisions; their dashboards work off aggregate metrics already.

The full role matrix lives in [`fe_admin_rbac_integration_guide.md`](./fe_admin_rbac_integration_guide.md#permission-matrix-system-roles).

---

## 3. Behavioural matrix

The `organization_id` query param resolves through a single backend helper that produces the same result everywhere:

| Caller | `organization_id` value | Outcome |
|---|---|---|
| Org user / API key | omitted | scoped to caller's own org (existing behaviour) |
| Org user / API key | matches caller's own org | scoped to caller's own org |
| Org user / API key | **any other UUID** | **403** `AUTHORIZATION_ERROR` |
| Admin without the perm | omitted **or** any UUID | **403** `AUTHORIZATION_ERROR` |
| Admin with the perm | omitted | cross-org (all orgs) |
| Admin with the perm | specific UUID | scoped to that org |
| Anyone | non-UUID string | **422** validation error |

> The "org user passes another org's UUID → 403" rule is deliberate. Silently ignoring or returning empty would be a confusing footgun for FE developers. Pass nothing, or pass your own.

---

## 4. Affected endpoints

### 4.1 Score requests

| Endpoint | Org caller | Admin caller |
|---|---|---|
| `GET /v1/score-requests` | own org list | scoped or cross-org list (per `organization_id`) |
| `GET /v1/score-requests/{tracking_id}` | own org only — 404 otherwise | any tracking ID; access is audit-logged |
| `GET /v1/score-requests/{tracking_id}/scoring-result` | same | same |
| `GET /v1/score-requests/stats?period=` | own org stats | scoped or platform-wide stats |

All four accept the `organization_id` query param with the matrix from §3. The `period` semantics are unchanged (`today` · `7d` · `30d` · `90d`).

### 4.2 Batch scoring

| Endpoint | Org caller | Admin caller |
|---|---|---|
| `GET /v1/score/batch` | own org list | scoped or cross-org list |
| `GET /v1/score/batch/{job_id}` | own org only | any job; audit-logged |
| `GET /v1/score/batch/{job_id}/results` | same | same |

Cancellation (`POST /v1/score/batch/{job_id}/cancel`) does **not** accept `organization_id`. Admins cannot cancel batch jobs they don't own.

---

## 5. 🆕 `GET /admin/score-requests/stats` — platform-wide overview

A dedicated endpoint built for the admin overview dashboard's stat tile. Returns the same `current` / `previous` / `trend` / `needs_attention` shape as the org endpoint **plus** a `by_org[]` breakdown when no `organization_id` is set.

### Request

```http
GET /admin/score-requests/stats?period=7d
Authorization: Bearer <admin-jwt>
```

| Param | Required | Notes |
|---|---|---|
| `period` | ✅ | `today` · `7d` · `30d` · `90d` |
| `organization_id` | ❌ | Omit for platform-wide aggregates; pass to scope to a single org (response is then identical to what an org user would see at `/v1/score-requests/stats`) |

**Permission**: `admin.score_requests.read`. Org callers get 403 even if they pass their own org id — this is an admin-only namespace.

### Response `200`

The response shape is **exactly the same** as the org-side `/v1/score-requests/stats` — `current`, `previous`, `trend`, `needs_attention`. The FE can reuse the same components for both org-side and admin-side stat tiles.

```jsonc
{
  "period": "7d",
  "generated_at": "2026-04-24T15:04:20.269477Z",
  "current": {
    "total_requests": 1820,
    "decided": 1701,
    "avg_credit_score": 614,
    "median_credit_score": 622,
    "approval_rate_pct": 51.2,
    "decision_counts": {
      "APPROVE": 712, "CONDITIONAL_APPROVE": 158,
      "DECLINE": 612, "REFER": 184, "ERROR": 28, "FRAUD_HOLD": 7
    },
    "score_distribution": [
      { "range": "300-499", "count": 132 },
      { "range": "500-579", "count": 348 },
      { "range": "580-669", "count": 612 },
      { "range": "670-739", "count": 478 },
      { "range": "740-850", "count": 224 }
    ]
  },
  "previous": {
    "total_requests": 1640, "decided": 1502,
    "avg_credit_score": 611, "approval_rate_pct": 49.8
  },
  "trend": {
    "total_delta_pct": 11.0, "approval_delta_pp": 1.4, "score_delta": 3
  },
  "needs_attention": { "referred": 184, "pending_or_processing": 12, "failed": 6 }
}
```

### Empty state

If the platform has zero requests in the window: `current` is all zeros, `previous` is empty, and `trend.*` are all `null`. **Never 404.**

### Building per-org tables

This endpoint deliberately does **not** return a per-org breakdown.  An unbounded list bolted onto a stats endpoint would force every caller to download every org's row even when they only render the top of a sortable table; it would also overlap with the existing paginated org list for no reason.

If the FE needs a per-org table (sortable, paginated, filterable), the pattern is:

1. **Get a paginated page of orgs** from the existing endpoint:
   ```http
   GET /admin/organizations?page=1&per_page=20
   ```
2. **For each visible row**, fetch its scoped stats:
   ```http
   GET /admin/score-requests/stats?period=7d&organization_id={org.id}
   ```

A 20-row page is 1 + 20 round-trips (cheap stats queries — all indexed aggregates). React Query parallelises the second batch automatically; latency is dominated by the slowest single query, not their sum. See §9 for the example hook.

---

## 6. Mutations remain org-only

By design, every write path stays strictly scoped to the caller's own organisation. None of these accept `organization_id`:

- `POST /v1/score-requests`
- `POST /v1/score-requests/{id}/override`
- `POST /v1/score-requests/{id}/outcome`
- `POST /v1/score-requests/{id}/performance`
- `POST /v1/score/batch`
- `POST /v1/score/batch/{id}/cancel`

Override / outcome / cancel are decisions the org owns. If a support scenario surfaces that needs admin-initiated scoring on behalf of an org, it'll be a separate, explicitly-named endpoint with its own audit trail — not a flag added to these.

---

## 7. Audit logging

Every admin cross-org read produces exactly one row in `audit_logs`:

| Field | Value |
|---|---|
| `actor_type` | `"admin"` |
| `actor_id` / `actor_email` | the admin |
| `organization_id` | the org being read (or `null` for cross-org list / stats) |
| `action` | `"score_requests.read_cross_org"` or `"batch_scoring.read_cross_org"` |
| `resource_type` | `"score_requests"` or `"batch_scoring"` |
| `resource_id` | tracking ID or job ID for detail reads; `null` for list / stats |
| `request_id` | propagated from the request middleware |

Org-side reads (a customer reading their own data) do **not** write audit rows. The audit table is for privileged-access trace, not business analytics.

The FE doesn't need to render these directly today — they exist for compliance and incident response. A future "Admin activity" tab could surface them via a new endpoint when the team wants it.

---

## 8. TypeScript

```typescript
// Shared shape for any cross-org-aware list/detail/stats call
export interface CrossOrgScopedParams {
  organization_id?: string;   // admin-only; org users either omit or pass own id
}

// Augmented org-side list params (existing decision/status filters retained)
export interface ListScoreRequestsParams extends CrossOrgScopedParams {
  page?: number;
  per_page?: number;
  status?: ScoreRequestStatus;
  decision?: ScoreDecision[] | ScoreDecision;
}

// /admin/score-requests/stats returns the same shape as the org endpoint —
// no separate type needed, just reuse ScoreRequestStatsResponse.
```

---

## 9. React Query examples

```tsx
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';

// ── Admin overview tile — platform-wide ────────────────────────────
export function usePlatformScoreStats(period: ScoreDashboardPeriod = '7d') {
  return useQuery<ScoreRequestStatsResponse>({
    queryKey: ['admin', 'score-requests', 'stats', period],
    queryFn: () =>
      api.get(`/admin/score-requests/stats?period=${period}`).then(r => r.data),
    refetchInterval: period === 'today' ? 60_000 : 5 * 60_000,
    staleTime: period === 'today' ? 30_000 : 2 * 60_000,
    refetchOnWindowFocus: false,
  });
}

// ── Admin: scoped stats for a single org (drill-down) ──────────────
export function useOrgScoreStatsAsAdmin(
  orgId: string,
  period: ScoreDashboardPeriod = '30d',
) {
  return useQuery<ScoreRequestStatsResponse>({
    queryKey: ['admin', 'score-requests', 'stats', period, orgId],
    queryFn: () =>
      api
        .get('/admin/score-requests/stats', {
          params: { period, organization_id: orgId },
        })
        .then(r => r.data),
    enabled: Boolean(orgId),
    staleTime: 2 * 60_000,
  });
}

// ── Admin: paginated per-org table (fan-out pattern) ───────────────
// Fetches a page of orgs, then queries scoped stats for each in
// parallel.  React Query parallelises and caches per-org keys so
// re-renders, sort changes, etc. don't re-fetch.
export function useOrgsWithStatsPage(
  page: number,
  perPage: number,
  period: ScoreDashboardPeriod = '7d',
) {
  const orgs = useQuery({
    queryKey: ['admin', 'organizations', page, perPage],
    queryFn: () =>
      api
        .get('/admin/organizations', { params: { page, per_page: perPage } })
        .then(r => r.data),
    keepPreviousData: true,
  });

  const orgIds: string[] = orgs.data?.items.map((o: { id: string }) => o.id) ?? [];

  const stats = useQueries({
    queries: orgIds.map((id) => ({
      queryKey: ['admin', 'score-requests', 'stats', period, id],
      queryFn: () =>
        api
          .get('/admin/score-requests/stats', {
            params: { period, organization_id: id },
          })
          .then(r => r.data),
      staleTime: 2 * 60_000,
    })),
  });

  // Zip orgs[] ↔ stats[] for the FE renderer.
  const rows = orgs.data?.items.map((org, i) => ({
    org,
    stats: stats[i].data,
    isLoading: stats[i].isLoading,
  })) ?? [];

  return { rows, total: orgs.data?.total, isLoading: orgs.isLoading };
}

// ── Admin: list any org's score requests ───────────────────────────
export function useOrgScoreRequestsAsAdmin(orgId: string, page = 1) {
  return useQuery({
    queryKey: ['admin', 'score-requests', orgId, page],
    queryFn: () =>
      api
        .get('/v1/score-requests', {
          params: { organization_id: orgId, per_page: 50, page },
        })
        .then(r => r.data),
    enabled: Boolean(orgId),
    keepPreviousData: true,
  });
}

// ── Admin: deep-link into any org's batch job ──────────────────────
export function useBatchJobAsAdmin(jobId: string) {
  return useQuery({
    queryKey: ['admin', 'batch', jobId],
    queryFn: () =>
      api.get(`/v1/score/batch/${jobId}`).then(r => r.data),
    enabled: Boolean(jobId),
  });
}
```

`useBatchJobAsAdmin` works for any caller — admin or org — because the backend resolves cross-org access from the JWT, not from a query param. Same for the detail and result endpoints.

---

## 10. Error reference

```json
// 401 — no/expired token
{ "error": { "code": "AUTHENTICATION_ERROR", "message": "...", "details": {} } }

// 403 — missing the cross-org permission, or org user passing another org's id
{ "error": {
    "code": "AUTHORIZATION_ERROR",
    "message": "Cross-organisation reads require admin permission",
    "details": {}
} }

// 403 — admin without admin.score_requests.read trying to use the param
{ "error": {
    "code": "AUTHORIZATION_ERROR",
    "message": "Permission required to read this resource: admin.score_requests.read",
    "details": {}
} }

// 404 — admin scoping to a tracking_id that doesn't exist anywhere
{ "error": { "code": "NOT_FOUND", "message": "Score request '...' not found", "details": {} } }

// 422 — non-UUID organization_id
{ "error": {
    "code": "VALIDATION_ERROR",
    "message": "organization_id is not a valid UUID: 'not-a-uuid'",
    "details": {}
} }
```

The FE's existing axios interceptor handles all four shapes already.

---

## 11. UX notes

- **Show the org name, not the UUID.** `by_org[].name` is the human label; only round-trip the `organization_id` for navigation. The org list endpoint (`GET /admin/organizations`) is the authoritative source if you need to render an org switcher.
- **Don't render `by_org` when scoped.** When the operator picks a specific org from the platform overview, the request switches to `?organization_id=…` and `by_org` becomes `[]`. Hide the breakdown table; only show `current` / `previous` / etc.
- **Audit-aware UI is optional but encouraged.** Subtle "you're viewing another organisation's data" banner when the current admin is scoped to a non-self org is a nice touch for compliance-conscious operators. The same banner is the right place to expose a link out to the audit log filtered by `actor_id = me`.
- **Cache differently.** Org users' stats can cache aggressively; admin cross-org stats should re-fetch when the operator switches scope, since the data they care about changes per click. Use the React Query `queryKey` patterns above (period + org id in the key) and you get this for free.

---

## 12. Cut-over

The org-side dashboard guide ([`fe_dashboard_integration_guide.md`](./fe_dashboard_integration_guide.md)) is unchanged. Org users continue calling the same endpoints with their own scoped tokens — they just **may** now pass their own `organization_id` redundantly without breaking anything.

The admin overview integration is net-new, gated by the permissions in §2. Roll out behind a feature flag if you want; nothing on the backend gates by deploy generation, only by RBAC.
