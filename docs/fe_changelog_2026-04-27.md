# Frontend Changelog — 2026-04-27

> Two backend changes the FE should know about. Both are independent — read whichever section you care about. Each section is self-contained (no jumping between files needed).
>
> Canonical integration guides remain authoritative; this doc is just the delta.

---

## Change 1 — `organization` block now embedded on every read response

### What changed

Every score-request and batch-job read response now carries a thin `organization` summary so list and detail rows can render the org name without a follow-up lookup.

**Net-additive.** Your existing types just need the new field added; nothing changed about the rest of the response.

### Endpoints affected

| Endpoint | Where the field appears |
|---|---|
| `GET /v1/score-requests` | each `items[i].organization` |
| `GET /v1/score-requests/{tracking_id}` | top-level `organization` |
| `POST /v1/score-requests` | top-level `organization` (response) |
| `GET /v1/score/batch` | each `items[i].organization` |
| `GET /v1/score/batch/{job_id}` | top-level `organization` |
| `GET /v1/score/batch/{job_id}/results` | top-level `organization` |

### Shape

```typescript
export interface OrganizationSummary {
  id: string;             // uuid
  name: string;
  short_name: string | null;
}
```

`short_name` is nullable (legacy orgs don't have one). Fall back to `name` for display when it's `null`.

### Example (list row, before vs. after)

**Before**:
```json
{
  "request_id": "SCR-83069FD32543",
  "tracking_id": "SCR-83069FD32543",
  "status": "completed",
  "applicant_name": "John K.",
  "loan_amount": 76000,
  "score_value": 612,
  "created_at": "2026-04-25T10:00:00Z"
}
```

**After**:
```json
{
  "request_id": "SCR-83069FD32543",
  "tracking_id": "SCR-83069FD32543",
  "status": "completed",
  "organization": {
    "id": "a12f3d9c-8d83-4156-9372-383c3075fd49",
    "name": "Viktrust Bank",
    "short_name": "viktrust"
  },
  "applicant_name": "John K.",
  "loan_amount": 76000,
  "score_value": 612,
  "created_at": "2026-04-25T10:00:00Z"
}
```

### Why it was added

For cross-org admin views (admins drilling into rows belonging to organisations other than their own — see [`fe_admin_cross_org_reads_guide.md`](./fe_admin_cross_org_reads_guide.md)), the FE previously had only a UUID and would have needed to N+1 against `/admin/organizations/{id}` to render names. Now the org summary travels with the row.

### What this means for org-side callers

For org users / API keys, `organization` will always be your own org. Feel free to ignore the field — it's there for shape consistency between org-side and admin-side responses. No UI change required unless you want to show it.

### What this means for admin callers

Drop any in-flight code that resolves `organization_id` to a name post-fetch. Use `row.organization.name` directly. Cuts a request per row and removes a class of "name didn't load yet" UI flicker.

### Where it's documented

Permanent home: [`fe_dashboard_integration_guide.md` §1b](./fe_dashboard_integration_guide.md) and [`fe_admin_cross_org_reads_guide.md`](./fe_admin_cross_org_reads_guide.md).

---

## Change 2 — `by_org` removed from `/admin/score-requests/stats`

### What changed

The platform stats endpoint **no longer returns a `by_org[]` field**. The response is now exactly the same shape as the org-side `/v1/score-requests/stats` — `current` / `previous` / `trend` / `needs_attention`.

### Why it was removed

The previous design conflated two concerns:

1. **Aggregate stats** for a tile (totals, trends).
2. **A paginated list of orgs with their stats** for a table.

Bundling them meant `by_org[]` was unbounded — a platform with 500 orgs returned a 500-row response on every poll, with no pagination, no sort control, and no filter. The FE also already has a paginated org list at `/admin/organizations`, so duplicating that surface inside a stats response was unnecessary.

### What you need to do if your code reads `body.by_org`

1. **Remove all references** to `body.by_org` from the platform stats response.
2. **Drop these types** if you defined them locally:
   - `OrgBreakdownEntry`
   - `PlatformScoreRequestStatsResponse`

   The endpoint now returns `ScoreRequestStatsResponse` (the same type as the org endpoint).
3. **If you were rendering a per-org table**, switch to the fan-out pattern in the next section.

### Building per-org tables — the new pattern

For "list of orgs with their score-request activity" UIs:

1. **Page the orgs** from the existing endpoint:
   ```http
   GET /admin/organizations?page=1&per_page=20
   ```
2. **For each visible row**, fetch its scoped stats:
   ```http
   GET /admin/score-requests/stats?period=7d&organization_id={org.id}
   ```

A 20-row page is 1 + 20 round-trips. All stats queries are indexed aggregates and individually fast. React Query parallelises the second batch automatically — the page's perceived latency is the slowest single query, not the sum.

### React Query example (recommended)

```tsx
import { useQuery, useQueries } from '@tanstack/react-query';
import { api } from '@/lib/api';

export function useOrgsWithStatsPage(
  page: number,
  perPage: number,
  period: ScoreDashboardPeriod = '7d',
) {
  // 1. Paginated org list — one query
  const orgs = useQuery({
    queryKey: ['admin', 'organizations', page, perPage],
    queryFn: () =>
      api
        .get('/admin/organizations', { params: { page, per_page: perPage } })
        .then(r => r.data),
    keepPreviousData: true,
  });

  const orgIds: string[] = orgs.data?.items.map(o => o.id) ?? [];

  // 2. Fan-out: one stats query per visible org, parallelised + cached
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

  // 3. Zip into renderable rows
  const rows = orgs.data?.items.map((org, i) => ({
    org,
    stats: stats[i].data,
    isLoading: stats[i].isLoading,
  })) ?? [];

  return { rows, total: orgs.data?.total, isLoading: orgs.isLoading };
}
```

### What didn't change

- The `organization_id` query param on `/admin/score-requests/stats` still works — pass it for scoped (single-org) stats, omit it for platform-wide aggregates.
- The org-side `/v1/score-requests/stats` is unchanged.
- All other cross-org read endpoints (list, detail, batch) are unchanged.

### Where it's documented

Permanent home: [`fe_admin_cross_org_reads_guide.md` §5 + §9](./fe_admin_cross_org_reads_guide.md).

---

## Migration checklist

Run through these before merging the next FE deploy that depends on either change:

- [ ] Add `organization?: OrganizationSummary` to your TS types for score-request and batch-job rows + detail responses.
- [ ] Remove any `body.by_org` references on the platform stats endpoint.
- [ ] Remove local `OrgBreakdownEntry` / `PlatformScoreRequestStatsResponse` type definitions.
- [ ] If you had a per-org table component, swap to the fan-out hook pattern shown above.
- [ ] Optional: surface `organization.name` on cross-org admin rows so users can tell whose data they're viewing at a glance.

---

## Questions / issues

Open a ticket referencing this changelog. Both changes are live on the API — no feature flag involved; the responses already include / exclude the fields described above.
