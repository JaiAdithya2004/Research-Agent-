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

## 5. Highlight offsets are not documented as code points

- Needed: the unit of `SpanOut.start` / `end` (`highlights` on `RunDetailOut` and `QueueItem`). The
  backend computes them with Python `re` on a `str`, so they count Unicode code points, not
  JavaScript UTF-16 units. The schema only says `integer >= 0`.
- Screen: Answer viewer, Review queue.
- Workaround: `src/lib/highlight.ts` slices by code point (`Array.from`), tested with an emoji.

## 6. The review endpoint has no idempotency

- Needed: `POST /mentions/{run_id}/review` accepts no `Idempotency-Key`, so a retry after a lost
  response, or a reload mid-send, can append a duplicate review (reviews are append-only).
- Screen: Answer viewer, Review queue.
- Workaround: `src/lib/review-outbox.ts` marks an entry "sending" before the POST and, before any
  re-send, asks `GET /runs/{id}` whether the latest review already matches (action, corrected value,
  reason, and a timestamp not older than the click minus 2 minutes). A duplicate is still possible
  only if the server saved the review, the response was lost, AND the verify lookup also failed.

## 7. Review queue items lack `degraded`, and the queue has no total

- Needed: whether the run answered without search because search was down (`degraded`), and the
  total number of flagged mentions (`QueuePage` has only `next_cursor`).
- Screen: Review queue.
- Workaround: the card shows the run's `search_mode`; the "capped" state is shown as "Showing the
  first N, more are waiting" from `next_cursor`. The run page (`RunDetailOut.degraded`) does show
  the degraded state.

## 8. No endpoint says who the current reviewer is

- Needed: the authenticated principal's name, to show "you are reviewing as ..." before submitting.
- Screen: Review forms.
- Workaround: the form says the server records the reviewer; the name appears after saving, from
  `ReviewDetail.reviewer`. The client never sends a reviewer.
