# Frontend contract

Read this together with `../AGENTS.md` (frontend rules), `../SPEC.md` ("Frontend scope" and
"Frontend acceptance criteria") and `openapi.json` (the exact API shapes). This file records the
decisions and the meanings that the types alone do not carry.

## Rules (from AGENTS.md, restated because they are easy to break)

- The API client is **generated** from `openapi.json`. Never hand-write or hand-edit generated
  files. `openapi.json` itself is generated from the backend by
  `uv run python scripts/export_openapi.py` (check mode: `--check`).
- **No business logic in components.** Data fetching and derived state live in hooks and plain
  modules; components only render.
- **Every data view implements all of these states**: loading, empty, error, partial, capped,
  degraded, failed. Each one is a real, distinct, tested UI (not one generic message).
- **Accessibility is part of done**: labels, visible focus states, `aria-live` for progress,
  keyboard navigation, WCAG AA contrast, usable at 375px width.
- TypeScript strict. Tailwind. shadcn/ui. TanStack Query. Vitest. Playwright.

## Architecture: the browser never holds the API key

The backend authenticates with an `X-API-Key` header. The key must **not** reach the browser.
Use a backend-for-frontend proxy:

- A Next.js route handler at `/api/[...path]` forwards the request to `API_BASE_URL` and adds
  `X-API-Key: $AUDIT_API_KEY` (server-only env vars, never `NEXT_PUBLIC_*`). It passes through
  method, query string, JSON body, `Idempotency-Key`, and `Last-Event-ID`; it strips cookies.
- The same proxy streams `GET /audits/{id}/events` (Server-Sent Events) without buffering.
  Because the proxy adds the key, the browser can use the standard `EventSource` against
  `/api/audits/{id}/events`; `EventSource` sends `Last-Event-ID` itself when it auto-reconnects.
  If you must reconnect manually, pass `?last_event_id=<n>`.
- `web/.env.example` documents `API_BASE_URL` (default `http://127.0.0.1:8000`) and
  `AUDIT_API_KEY`.

## Endpoints (shapes are in `openapi.json`)

`POST /audits` (needs an `Idempotency-Key` header; generate a UUID per submission and reuse it on
retry), `GET /audits` (cursor pagination: `limit`, `cursor`, filters `status`, `brand`),
`GET /audits/{id}`, `GET /audits/{id}/runs`, `GET /runs/{id}`, `GET /audits/{id}/sources`,
`GET /audits/{id}/sources/{source_id}`, `GET /audits/{id}/gaps`, `GET /audits/{id}/events` (SSE),
`POST /mentions/{run_id}/review`, `GET /review/queue`, `GET /evals/runs`, `GET /health`.

Status codes you must handle: `202` created, `200` repeat of the same `Idempotency-Key` (the
original audit, not an error), `401`, `404`, `409` (review of a run with nothing to review), `422`
(validation; the body has `detail`), `429` (rate limit or daily budget; the body has a clear
`detail` message and a `Retry-After` header; show the message), `503` (could not queue; retry
with the same key).

## Meanings that must never be mixed up

**A page we could not read is not a page without the brand.** This is the most important rule in
the UI.

- `brand_on_page: true` means the page was read and the brand is in its text (a verified quote
  is given).
- `brand_on_page: false` means the page was read and the brand is **not** there.
- `brand_on_page: null` means **we could not read the page**. Look at `fetch_status`:
  `unreachable`, `blocked`, `paywalled`, `too_large`, `error`. This must look clearly different
  from `false` (different color, icon and wording, not just text). Never render `null` as "no".
- `fetch_reason` is a coarse code (`http_denied`, `http_not_found`, `timeout`,
  `robots_disallowed`, `blocked_by_policy`, `paywall_marker`, `js_or_empty`, ...). Show a friendly
  label; unknown codes fall back to the raw value.

**Citations**: `source: "grounding_metadata"` means the provider returned the URL as a structured
citation (trusted). `source: "answer_text"` / `unverified: true` means the URL appears only in the
answer prose and was never confirmed (show an "unverified" badge; it is never fetched).
`evidence: null` means the page was never researched (a cap was hit, or research was off).

**Verdicts**: a mention is *confirmed* when the extractor found the brand AND the verifier
verified it, or a human approved it. `verdict.status: "flagged"` with no `review` means a human
decision is pending. A flagged mention is **not** counted in `mention_rate` until approved.
`RunSummary.brand_mentioned` is the effective outcome (`null` = no usable answer).

**Audit status**: `running`, `complete`, `capped` (a limit stopped it: see `caps_hit`:
`cost_cap`, `fetch_cap`, `domain_cap`, `max_steps`; results are partial), `failed` (no usable
answer at all). `degraded: true` means search was unavailable for some runs and the audit fell
back to no-search for them. "Partial" means some runs failed (`summary.failed_runs > 0`) or the
audit is `running`/`capped` with a subset of results.

**Stability**: `summary.question_stability[]` has one entry per question, model and search mode,
with `outcomes` (one per repeat: `true`, `false`, or `null` for a failed repeat) and `agreement`
(null when fewer than two usable repeats). Draw one dot per outcome (filled = brand confirmed,
hollow = not mentioned, a distinct "failed" mark for `null`). Never average disagreement away in
the UI; show the per-question dots and the number.

**Mode delta**: `summary.mode_delta` (grounded rate minus off rate) exists only when the audit ran
`search_mode: "both"`. `summary.mention_rate_by_mode` has both rates. Show the delta with its sign
and both rates.

**Costs**: `summary.total_cost_usd` is the audit's actual spend; the default cap is $0.75.

## Cost estimate on the new-audit form

There is no pricing endpoint; the estimate is a documented client-side calculation in
`src/lib/estimate.ts` with unit tests. Use these stated assumptions and display them to the user:

- runs = questions x repeats x models (assume 2) x modes (1, or 2 for `both`)
- per answer call: 1,500 input and 800 output tokens at $0.75 / $3.75 per million (Gemini) as the
  reference rate; per extraction call: 1,200 input and 150 output tokens
- each grounded run costs `queries_per_run` search queries (assume 3) at $0.014 each
- `cited_pages` research: assume up to `min(30, 3 x grounded runs)` page fetches (fetching is free
  but counts toward the 30-fetch cap) plus one classification call per new unknown domain
- show the breakdown (model calls, extraction calls, search calls, page fetches), the total, and
  a warning when the total exceeds the $0.75 default cap ("the audit will stop at the cap and
  return partial results")

## Developing against the real backend

```
docker compose up -d --wait
uv run python -m alembic upgrade head
uv run python -m app.auth create --name dev      # prints a key once; put it in web/.env.local
uv run python -m app.api                           # http://127.0.0.1:8000
uv run python -m app.worker
```

Tests must **not** need the backend: Vitest mocks `fetch`; Playwright intercepts network requests
(`page.route`) and, for SSE, serves a scripted `text/event-stream` body.

## Delivery rules

- One commit per phase, message ending with
  `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Do not modify Python code, migrations or `prompts/`. If the API lacks something the UI needs,
  write it down in `web/API_GAPS.md` and work around it; do not change the backend.
- Finish with: what you changed, what you did **not** verify, and what you assumed.
