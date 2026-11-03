# API gaps found by the frontend

Things the UI needed that `openapi.json` does not provide or declare. Each entry says what, which
screen, and the workaround. The backend is not changed by the frontend work.

## 1. 401 is not declared, and 429 only on POST /audits

- Needed: the `401` (bad key) response on every endpoint, and `429` (rate limit or daily budget,
  with `Retry-After`) wherever the limiter applies (it is declared on `POST /audits` only), so the
  generated client types them.
- Screen: New audit form (shows the server message on 429), every data view (401 hint).
- Workaround: `src/lib/create-audit.ts` and `src/lib/api/errors.ts` switch on `response.status`
  and read `{detail}` from the error body; `Retry-After` is read from the response headers.

## 2. Validation errors have two shapes but one declared schema

- Needed: `422` is declared as `ErrorOut` (`detail: string`), but FastAPI's own request
  validation returns `detail: [{loc, msg, type}]` (`HTTPValidationError`), while hand-raised 422s
  return a string.
- Screen: New audit form (field-level errors).
- Workaround: `src/lib/api/errors.ts` accepts both shapes.

## 3. No total number of planned runs on the audit resource

- Needed: the number of runs the audit will make, to show "12 of 24 runs finished" while the audit
  is running. `AuditSummary.total_runs` only counts runs recorded so far.
- Screen: Audit detail (progress bar).
- Workaround: take `planned_steps` from the `audit_started` / `audit_resumed` SSE event (the
  stream replays from the first event on connect). Falls back to an indeterminate bar when the
  event has not arrived yet.

## 4. No pricing endpoint

- Needed: model and search prices, so the new-audit estimate does not hard-code them.
- Screen: New audit form.
- Workaround: documented constants in `src/lib/estimate.ts` (from `CONTRACT.md`).
