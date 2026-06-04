# Frontend Integration — Score Request Actions

Complete contract for every action the frontend can perform on a score request,
with request/response shapes, business rules, and **exact error handling**.

Base path: `/v1/score-requests`  ·  Auth: `Authorization: Bearer <access_token>`
(from `POST /auth/login`). All bodies are JSON.

---

## 0. Error envelope — READ THIS FIRST

The API returns **two different error shapes**. Your error handler must cope with both.

**(A) Business / app errors** (most non-2xx):
```json
{ "error": { "code": "AUTHORIZATION_ERROR", "message": "Permission required: score_requests.override", "details": {} } }
```

**(B) Request body validation errors** (HTTP 422, malformed/missing fields):
```json
{ "error": "validation_error", "detail": "loan_request → amount: Input should be greater than 0" }
```

So: if `typeof body.error === "object"` → use `body.error.code` / `body.error.message`.
If `typeof body.error === "string"` → it's a 422; show `body.detail`.

### Status code → meaning (whole API)
| HTTP | `error.code` | Meaning | FE action |
|----|----|----|----|
| 400 | (varies) | Business rule violation (e.g. override on non-REFER) | Show `message`; don't retry |
| 401 | `AUTHENTICATION_ERROR` | Missing/expired/invalid token | Refresh token → retry once; else send to login |
| 403 | `AUTHORIZATION_ERROR` | Missing permission | Hide/disable the action for this user |
| 403 | `ORG_SUSPENDED` | Caller's org is suspended | Global "account suspended" state |
| 404 | `NOT_FOUND` | Unknown `request_id` (or not in caller's org) | "Not found" |
| 409 | `CONFLICT` / `IDEMPOTENCY_CONFLICT` | Duplicate (e.g. idempotency key) | Treat as already-done |
| 422 | `validation_error` (string shape) | Bad request body | Map `detail` to the offending field |
| 429 | `RATE_LIMIT_EXCEEDED` | Too many requests | Back off; honor `Retry-After` |
| 502 | `SCORING_ERROR` | AI/scoring pipeline failure | "Scoring temporarily unavailable; retry" |
| 500 | `INTERNAL_ERROR` | Unexpected | Generic error toast |

Rate-limit headers are present on successful org-scoped responses:
`X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`.

---

## 1. Create a score request
`POST /v1/score-requests`  ·  permission `score_requests.create`  ·  **201 Created**

Requires a prior **bureau-lookup** step — pass its `consumer_id` in `bureau_data`
(the raw bureau report is cached server-side under that id).

Headers: optional `Idempotency-Key: <uuid>` — **strongly recommended**. Reuse the
**same** key when retrying a failed/timed-out create to avoid duplicate scoring.

Request body:
```json
{
  "reference_id": "LOAN-2026-00123",
  "applicant": {
    "full_name": "Kwame Mensah",
    "date_of_birth": "1992-04-15",
    "national_id_number": "GHA-123456789-0",
    "phone": "+233241234567",
    "account_number": "1002003004"
  },
  "loan_request": { "amount": 40000, "tenure_months": 24, "purpose": "asset_finance" },
  "bureau_data": { "consumer_id": "<from bureau-lookup>" },
  "metadata": { "channel": "web_portal", "callback_url": "https://you/cb" }
}
```
Field rules: `full_name` 2–200 chars; `date_of_birth` is a date (`YYYY-MM-DD`);
`amount > 0`; `tenure_months` 1–360.

Success: returns the full score request (`request_id` = tracking id `SCR-…`,
`status`, `applicant`, `loan_request`, `scoring_result`, `timestamps`). A policy
hard-stop returns a `HardStopDeclineResponse` (still 201) — branch on its shape.

Errors: `422` bad body · `409 CONFLICT` duplicate idempotency key ·
`502 SCORING_ERROR` pipeline failure (retry with the **same** idempotency key) ·
`403 ORG_SUSPENDED`.

---

## 2. List score requests
`GET /v1/score-requests`  ·  permission `score_requests.list` (or admin `admin.score_requests.read`)

Query params:
- `page` (≥1, default 1), `per_page` (1–100, default 20)
- `status` = `pending | processing | completed | failed`
- `decision` = `APPROVE | CONDITIONAL_APPROVE | DECLINE | REFER | FRAUD_HOLD | ERROR`
  — **repeatable** to OR values: `?decision=REFER&decision=DECLINE`
- `organization_id` — **admin only**; org callers must omit it (cross-org → 403)

Returns `{ items: [ScoreRequestSummary], total, page, per_page }`.
Each summary carries `tracking_id, status, score_value, risk_category, organization, created_at`.

---

## 3. Dashboard stats
`GET /v1/score-requests/stats?period=today|7d|30d|90d`  ·  permission `score_requests.list`

Returns `{ period, generated_at, current{ total_requests, approval_rate_pct,
avg_credit_score, decision_counts{APPROVE,CONDITIONAL_APPROVE,DECLINE,REFER,FRAUD_HOLD,ERROR},
score_distribution[] }, previous, trend, needs_attention }`. Admin may add `organization_id`.

Poll cadence: `today` ~60s; `7d/30d/90d` ~5 min.

---

## 4. Get one score request (detail)
`GET /v1/score-requests/{request_id}`  ·  permission `score_requests.read` (or admin)

`request_id` is the tracking id (`SCR-…`). Returns `request_id, status,
organization, applicant, loan_request, scoring_result, timestamps`.
Top-level `score`/`model_info`/`recommendations` may be null — **read decision &
model output from the `scoring_result` block** (see §5).

Errors: `404 NOT_FOUND` (unknown id, or not in your org). Admin reads of any org
are allowed and audit-logged.

---

## 5. Get the full scoring result (4-model output)
`GET /v1/score-requests/{request_id}/scoring-result`  ·  permission `score_requests.read`

Returns `decision`, `condition_applied`, `scoring_timestamp`, `decline_reasons`,
`triggered_rules`, and the model blocks `credit_risk` (probability_of_default,
risk_tier, shap_contributions, decision_reason_codes), `fraud_detection`,
`loan_amount`, `income_verification`.

Errors: `404` — either the request is unknown, **or it has no result yet**
(`detail: "Scoring result not yet available. Current status: <status>"`). Branch:
if `status` is `pending/processing`, show "scoring in progress", not a hard error.

---

## 6. Override a REFER decision
`POST /v1/score-requests/{request_id}/override`  ·  permission `score_requests.override`

**Business rule:** only allowed when the current decision is **REFER**. APPROVE /
DECLINE are final and not overridable. Each request is overridable **once**.

Request body:
```json
{
  "decision": "APPROVE",
  "override_reason": "Strong guarantor; fresh graduate",
  "conditions": ["guarantor_required"],
  "approved_amount": 40000,
  "approved_tenure_months": 24
}
```
Rules: `decision` ∈ `APPROVE | CONDITIONAL_APPROVE | DECLINE`; `override_reason`
5–1000 chars; `conditions/approved_amount/approved_tenure_months` optional.

Success `200`: `{ message, request_id, previous_decision, new_decision, overridden_by }`.
After this, the request's `scoring_result.decision` reflects `new_decision`.

Errors:
- `400` — `"Can only override REFER decisions (current: DECLINE)"` → not REFER. Hide/disable the override button unless `decision === "REFER"`.
- `400` — `"Override decision must be one of: ..."` → invalid `decision`.
- `404` — unknown request / not your org. `403` — missing permission.

> FE tip: gate the override UI on `scoringResult.decision === "REFER"`.

---

## 7. Record the lending outcome (model feedback)
`POST /v1/score-requests/{request_id}/outcome`  ·  permission `score_requests.report_outcome`

What the lender actually decided (for the feedback loop). One outcome per request.

```json
{
  "decision": "approved",
  "approved_amount": 35000,
  "approved_tenure_months": 18,
  "interest_rate": 0.029,
  "decline_reason": null,
  "decision_notes": "Approved at branch",
  "decision_date": "2026-06-04"
}
```
`decision` ∈ `approved | declined | referred`. Success `200`:
`{ message, request_id, decision }`. Errors: `404` unknown/not your org; `403`.

---

## 8. Report loan performance (training feedback)
`POST /v1/score-requests/{request_id}/performance`  ·  permission `score_requests.report_performance`

Repayment status over time — can be reported multiple times (one row per report).
```json
{
  "status": "past_due_30",
  "days_past_due": 32,
  "outstanding_balance": 28500,
  "as_of_date": "2026-06-01"
}
```
`status` ∈ `current | past_due_30 | past_due_60 | past_due_90 | default | paid_off | write_off`;
`as_of_date` required. Success `200`: `{ message, request_id }`. Errors: `404`, `403`.

---

## Recommended FE error handler (pseudocode)

```ts
async function apiCall(method, path, { body, token, idempotencyKey } = {}) {
  const res = await fetch(BASE + path, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(token && { Authorization: `Bearer ${token}` }),
      ...(idempotencyKey && { "Idempotency-Key": idempotencyKey }),
    },
    body: body ? JSON.stringify(body) : undefined,
  });

  if (res.ok) return res.json();

  const payload = await res.json().catch(() => ({}));
  // Two envelope shapes:
  const isValidation = typeof payload.error === "string"; // 422
  const code = isValidation ? "VALIDATION_ERROR" : payload?.error?.code;
  const message = isValidation ? payload.detail : payload?.error?.message;

  switch (res.status) {
    case 401: await refreshToken(); throw new ApiError(code, message, { retryable: true });
    case 403: throw new ApiError(code, message, { forbidden: true });      // hide the action
    case 404: throw new ApiError(code, message, { notFound: true });
    case 409: return payload;                                              // idempotent: treat as done
    case 422: throw new ApiError(code, message, { fieldError: true });     // map to form field
    case 429: {
      const retryAfter = Number(res.headers.get("Retry-After") || 1);
      throw new ApiError(code, message, { retryAfter });                   // back off
    }
    case 502: throw new ApiError(code, message, { retryable: true });      // scoring pipeline
    default:  throw new ApiError(code || "INTERNAL_ERROR", message, {});
  }
}
```

### Gating rules to bake into the UI
- **Override button**: only enabled when `scoring_result.decision === "REFER"`.
- **Create**: always send an `Idempotency-Key`; reuse it on retry.
- **Scoring-result 404 with a pending/processing status** = "scoring in progress", not an error.
- **`organization_id` query param**: only send it for platform-admin users.
- **403 on an action** = the user's role lacks that permission → hide the control rather than letting them click and fail.
