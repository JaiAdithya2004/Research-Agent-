# AI Visibility Audit

**Do AI assistants recommend your brand?** This tool asks them the questions your customers ask,
records whether your brand comes up and where it ranks, reads the pages the answers relied on, and
reports which sites cite your competitors but not you.

One idea runs through the design: **the AI does the language work, and plain code checks every claim
it makes.** A mention counts only if its quote is found, word for word, in the AI's own answer.
Anything the code cannot confirm goes to a human reviewer.

## What you get

- A plain-language **visibility rating** (Strong, Mixed, Weak, Not visible) with the share of answers that mention your brand and your average rank.
- Every **answer, formatted** like a chat reply, with your brand's quote highlighted and a toggle for the exact text.
- **Stability**: each question is asked several times, and a "Results varied" flag shows when the AI disagrees with itself.
- A **sources view**: the pages the answers cited, what kind of site each is, and whether your brand is on the page.
- A **gap report**: domains cited for your competitors but never for you, ranked. This is where to get listed or reviewed.
- A **review queue** for claims the code could not verify.

## How it works

```
Browser ──► Next.js (BFF proxy adds the API key) ──► FastAPI ──► PostgreSQL
                                                        │ 202 + job
                                                        ▼
                                                     Worker
  per question × repeat:
    1. Tavily search  ──► results go to the model as a delimited, untrusted data block
    2. Answer model (no tools)
    3. Extractor model ──► Mention (a Pydantic model: mentioned, position, quote, competitors)
    4. Verifier (plain Python, no LLM) ──► verified | flagged, with a reason
  then: fetch the cited pages (SSRF-safe) ──► page check ──► source type ──► gap report
  progress streams to the UI over Server-Sent Events
```

Search modes: **with web search** (we run Tavily and hand the results to the model) and **without
search**. The API also supports Gemini's own grounding (`grounded`) and a side-by-side `both`.

### Design decisions

| Decision | Why |
|---|---|
| The verifier is deterministic and calls no model | A claim must be provable and reproducible. Same input, same verdict. |
| We run the search, the model gets no tools | We record exactly what the model saw. Citations are structured data, not parsed from prose. Less prompt-injection surface. |
| Every provider call goes through `app/llm/client.py` | Timeouts, retries, cost caps and tracing are enforced in one place. |
| Every model output is parsed into a Pydantic model | Malformed output fails loudly instead of flowing on as a loose dict. |
| Raw answers, grounding data and page snapshots are write-once | Evidence is never rewritten after the fact. |
| Fetched pages are data, never instructions | They are only passed to a model inside a delimited block, in a call with no tools. |
| A page that could not be fetched is never "brand absent" | It is recorded as unreachable, blocked or paywalled. |
| Reviews are append-only | A database trigger forbids edits. The only way to remove them is to delete the whole audit. |

The full spec is in [`SPEC.md`](SPEC.md) and the rules the code was built under are in [`AGENTS.md`](AGENTS.md).

## Tech stack

**Backend:** Python 3.11, FastAPI, Pydantic v2, async SQLAlchemy (psycopg 3), Alembic, PostgreSQL, Redis + ARQ, httpx, pytest, ruff, mypy (strict), uv.
**Frontend:** Next.js (App Router), TypeScript (strict), Tailwind, shadcn/ui, TanStack Query, Vitest, Playwright. The API client is generated from the backend's OpenAPI schema.
**Observability (optional):** Langfuse tracing, Sentry, structured JSON logs.

## Project layout

```
app/api        FastAPI routes, auth, SSE         app/verify    the deterministic verifier
app/audit      runner, metrics, events, repo     app/fetch     safe_fetch (SSRF-safe page fetching)
app/llm        provider gateway, Tavily, cost    app/research  source classifier, gap report
app/worker     ARQ worker and runtime            app/evals     eval CLI
alembic/       migrations                        prompts/      versioned extractor prompts
web/           Next.js app (BFF proxy in src/server)
docker/        API and worker images             docs/         Fly.io deployment guide
tests/         backend tests                     evals/        eval datasets and README
```

## Quick start (local)

```
uv sync --extra dev
copy .env.example .env          # then fill in GEMINI_API_KEY and TAVILY_API_KEY (and API_KEY_PEPPER)
docker compose up -d --wait     # Postgres and Redis (dev settings, no durability)
uv run python -m alembic upgrade head
uv run python -m app.auth create --name web      # prints an API key once
```

Run the API and the worker in two terminals:

```
uv run python -m app.api        # http://127.0.0.1:8000  (docs at /docs)
uv run python -m app.worker     # the ARQ worker that actually runs audits
```

Then the web app, with the key from above:

```
cd web
npm install
copy .env.example .env.local    # set API_BASE_URL=http://127.0.0.1:8000 and AUDIT_API_KEY=<the key>
npm run dev                     # http://localhost:3000
```

`POST /audits` only enqueues the job and returns `202`. To experiment without a worker, set
`QUEUE_BACKEND=inprocess` (audits then run inside the web process and are lost on restart).

### Configuration

Settings are read from environment variables only and validated at startup: a bad value stops the
process with a message naming the variable (never its value).

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Postgres, `postgresql+psycopg://...` |
| `API_KEY_PEPPER` | Required. Keys the stored hashes of API keys. Changing it invalidates every key. |
| `GEMINI_API_KEY`, `OPENAI_API_KEY` | Model providers (accessed only through `app/llm/client.py`) |
| `TAVILY_API_KEY` | Enables the "with web search" mode |
| `ANSWER_MODELS`, `EXTRACTOR_MODEL` | `provider:model`, comma separated |
| `EXTRACTOR_PROMPT_VERSION` | `v2` by default (`prompts/extract_<version>.txt`). Set `v1` to go back. |
| `QUEUE_BACKEND` | `arq` (durable, needs Redis) or `inprocess` (dev and the Render free tier) |
| `REDIS_URL` | Queue and rate limiting when `QUEUE_BACKEND=arq` |
| `DEFAULT_COST_CAP_USD`, `RATE_LIMIT_DEFAULT_PER_MIN` | Per-audit spend cap (default 0.75) and per-key request limit |
| `LANGFUSE_*`, `SENTRY_DSN` | Optional. With no keys, tracing and error reporting are off. `LANGFUSE_CAPTURE_CONTENT=1` also sends answer text. |
| Web: `API_BASE_URL`, `AUDIT_API_KEY` | Server-side only. Never prefix them with `NEXT_PUBLIC_`. |
| Web: `NEXT_PUBLIC_SHOW_EVALS` | Set to `1` at build time to show the team-only Evals page |

See `.env.example` for the full list.

## Commands (Windows, no make)

Each `make` target has a direct equivalent:

| make target       | command                                       |
|-------------------|-----------------------------------------------|
| `make test`       | `uv run pytest`                               |
| `make run`        | `uv run uvicorn app.api.main:app --reload`    |
| `make lint`       | `uv run ruff check .`                         |
| `make format`     | `uv run ruff format .`                        |
| `make typecheck`  | `uv run mypy`                                 |
| `make check`      | run lint, typecheck, then test, in that order |
| `make db-up`      | `docker compose up -d --wait`                 |
| `make db-down`    | `docker compose down`                         |
| `make db-migrate` | `uv run python -m alembic upgrade head`       |

Web: `npm run dev`, `test` (Vitest), `typecheck`, `lint`, `e2e` (Playwright), and `gen:api` to
regenerate the API client after a backend schema change (`gen:api:check` and CI fail if it drifts).

Notes for Windows:

- Use `python -m alembic`, not the `alembic` executable (Application Control blocked the shim).
- psycopg's async mode cannot use Windows' default event loop. `python -m app.api` and
  `python -m app.worker` select the right one; plain `uvicorn` only does with `--reload`.
- The compose database runs with `fsync=off` because durable writes were taking 20+ seconds per
  `CREATE DATABASE` on Docker Desktop. That is for dev and tests only, never production.

## API

All endpoints need an `X-API-Key` header, except `/health` and `/ready`.

| Endpoint | Purpose |
|---|---|
| `POST /audits` | Create an audit. Needs an `Idempotency-Key` header. `202` when created, `200` for a repeat of the same key and body (no new model, search or fetch calls), `422` for an invalid body or a key reused with a different body, `503` if the job could not be queued |
| `GET /audits` | List audits, newest first. Cursor pagination (`limit`, `cursor`), filters `status` and `brand` |
| `GET /audits/{id}` | The audit with its recomputed summary |
| `DELETE /audits/{id}` | Erase the audit with its runs, sources and reviews. `204`, `404`, or `409` while it is still running |
| `GET /audits/{id}/runs` | One row per run (question, model, mode, repeat) for the dot grid |
| `GET /runs/{id}` | One run: raw answer, extracted quote and highlight offsets, verdict and findings, citations with their fetch evidence |
| `GET /audits/{id}/sources` | Researched pages; filters `source_type`, `fetch_status`, `domain` |
| `GET /audits/{id}/sources/{source_id}` | One page snapshot with its text and timestamp |
| `GET /audits/{id}/gaps` | Domains cited for competitors but never for the brand, ranked |
| `GET /audits/{id}/events` | Live progress as Server-Sent Events, resumable with `Last-Event-ID` |
| `POST /mentions/{run_id}/review` | `approve`, `correct` or `reject` an extracted mention, with a required reason |
| `GET /review/queue` | Flagged mentions still waiting for a human decision |
| `GET /evals/runs` | Stored eval runs |
| `GET /health`, `GET /ready` | Liveness, and readiness (database reachable) |

Progress events are stored in Postgres with a gapless per-audit sequence. A client that reconnects
with `Last-Event-ID: <n>` receives exactly the events after `n`. A finished audit with nothing newer
answers `204`, which tells `EventSource` to stop reconnecting.

API keys are stored as HMAC-SHA256 hashes. Each key can carry its own rate limit and daily budget:
`uv run python -m app.auth create --name web --daily-budget 2 --rate-limit 30`.

### Review

`corrected_value` is required with `correct` and must be `mentioned`, `not_mentioned` or
`position=<n>`. The reviewer is the authenticated key's name, never a request field. Reviews are
appended (a database trigger forbids edits); the latest review of a run is the effective one. The
run's raw answer, grounding metadata, extracted mention and verdict are never modified, and every
review response carries the recomputed audit summary.

## How the numbers are defined

- **Confirmed mention**: the extractor said the brand was mentioned **and** the verifier verified
  it (or a human approved it). An unverified claim is not counted until a human approves it;
  `flagged_count` says how many are waiting.
- **Verification**: the quote must appear in the raw answer text and the brand or an alias must be in
  the prose (a brand inside a URL does not count). A claimed rank that the code proves wrong flags
  the mention. A rank it cannot confirm, because brands you did not list may sit ahead, does not hold
  the mention back: it is recorded with its reason and left out of the average.
- **mention_rate**: confirmed mentions over usable runs in the headline search mode (grounded when
  the audit ran `grounded` or `both`, otherwise off). Failed runs are excluded.
- **mode_delta** (only for `both`): grounded mention rate minus off mention rate.
- **avg_position**: mean rank over confirmed mentions whose rank the verifier confirmed (or a
  reviewer corrected).
- **stability**: per question, model and search mode, the share of repeats that agree with their own
  majority. It is reported **per group** (`question_stability`) and never averaged away; the
  headline is only the mean of those, and `repeats_compared` says how many groups had two or more
  usable repeats. With one repeat there is nothing to compare.
- **Latency P50/P90**: nearest-rank over the successful model calls.

An audit is `capped` when any limit stopped it (cost cap, 30 fetches, 5 per domain, max steps),
`failed` when no run produced a usable answer, and `complete` otherwise.

### Failure handling

Each model call has a 30 second timeout and up to 3 retries with exponential backoff (timeouts, 429,
502-504). Invalid model JSON gets exactly one repair attempt. A provider that fails 3 times in a row is
skipped and the audit continues with the others. If search is unavailable the call falls back to no
search and the run is marked `degraded`. Runs are saved as they finish, so a retried job resumes
without asking (or paying) the model again. A failed call logs its reason (error class and HTTP
status only, never keys or response bodies).

## Evals

See `evals/README.md` for the dataset formats and metric definitions.

```
uv run python -m app.evals run --prompt-version v1 --repeats 3 --out evals/results/v1.json
uv run python -m app.evals run --prompt-version v2 --repeats 3 --out evals/results/v2.json
uv run python -m app.evals compare evals/results/v1.json evals/results/v2.json
```

The output is a markdown table. Prompts are versioned files in `prompts/`, and every run records the
version it used. The web app's Evals page is for the team and is hidden unless
`NEXT_PUBLIC_SHOW_EVALS=1` is set at build time. Only small synthetic sample datasets ship in the repo.

## Safety

- Every outbound page fetch goes through `app/fetch/safe_fetch.py`: http(s) only, DNS resolved first
  with every address checked, the connection pinned to the checked IP, redirects re-checked, robots.txt
  honoured, 10 s timeout, 1 MB decoded-size cap, text content only.
- Fetched text is untrusted data. The only model call on it classifies the source type, with the
  text inside a delimited data block, no tools, no knowledge of the audited brand, and an
  enum-only output.
- The verifier, the page brand check and the gap report are plain code. No model writes them.
- `FetchResult.detail` can name internal addresses and is never returned by the API.
- No secrets in code or images. Logs never contain raw keys.

Known gaps: text hidden with external CSS classes or same-colour text still counts as visible, and a
site can serve our bot different content. Both are documented as expected failures in the tests.

## Deployment

The reference setup runs on free tiers: **web on Vercel, API on Render, database on Neon.**

**Render (API).** `render.yaml` is a Blueprint for the API only. Jobs run inside the API process
(`QUEUE_BACKEND=inprocess`), so no worker or Redis is needed. Limits of the free plan: the service
sleeps after about 15 minutes without traffic (the first request then takes 30-60 s), and an audit
still running when it sleeps or restarts is lost. For durable jobs use a worker built from
`docker/worker.Dockerfile` with `QUEUE_BACKEND=arq` and Redis.

1. Run migrations from your machine, with `DATABASE_URL` pointing at Neon (the free plan has no
   pre-deploy command): `uv run python -m alembic upgrade head`.
2. Create a key once, with the same `DATABASE_URL` and `API_KEY_PEPPER` you will give Render:
   `uv run python -m app.auth create --name web --daily-budget 2`.
3. In Render: New > Blueprint > pick this repo. Fill in the secrets it asks for (`DATABASE_URL`,
   `API_KEY_PEPPER`, `GEMINI_API_KEY`, `TAVILY_API_KEY`). Leave the optional ones empty.
4. Check `https://<service>.onrender.com/health`. Later updates need a manual deploy (`autoDeploy` is off).

**Vercel (web).** Import the repo with **Root Directory** `web`. Set `API_BASE_URL` to the Render
address and `AUDIT_API_KEY` to the key from step 2. Both are server-side only.

**Migrations are a release step, never app boot,** so many API machines starting at once cannot race.

For a durable setup (separate worker, Redis, release commands, egress policy and rollback notes), see
[`docs/deploy-fly.md`](docs/deploy-fly.md). Its deploy job in CI runs only when started by hand.

## Testing and CI

Over 1,200 backend tests and about 390 web tests, plus Playwright end-to-end tests. Every new behavior
is written test-first. CI (`.github/workflows/ci.yml`) runs ruff, strict mypy, the backend tests against
a real Postgres, the web typecheck, lint and tests, the browser tests, and builds all three images.

## Limits

- It audits what a model says when given search results. It does not query ChatGPT or Google's AI
  directly, so treat the numbers as a directional signal.
- On the free Render plan, audits are not durable (see Deployment).
- Extraction quality was tuned on stored answers. There are no real labeled datasets yet, so prompt
  versions are compared on small synthetic samples.
- Brands you did not list as competitors are not discovered automatically, so ranks that depend on
  them cannot be confirmed.
- Matching is accent-sensitive by design (`Trützschler` is not `Trutzschler`).
