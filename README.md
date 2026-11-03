# AI Visibility Audit Agent

Given a brand and buyer questions, the agent asks LLMs the way a user would (with and without
web search), extracts how the brand appears, verifies every claim against the raw text and the
cited sources, and returns a stability-aware visibility score plus a source gap report. Humans
review anything the code cannot verify. See `SPEC.md` for the spec and `AGENTS.md` for the working
rules.

## Quick start

```
uv sync --extra dev
copy .env.example .env          # then fill in GEMINI_API_KEY and/or OPENAI_API_KEY
docker compose up -d --wait     # Postgres and Redis (dev settings, no durability)
uv run python -m alembic upgrade head
```

Run the API and the worker in two terminals:

```
uv run python -m app.api        # http://127.0.0.1:8000  (docs at /docs)
uv run python -m app.worker     # the ARQ worker that actually runs audits
```

`POST /audits` only enqueues the job and returns `202`. For local experiments without a worker set
`QUEUE_BACKEND=inprocess` (audits then run inside the web process and are lost on restart).

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

Notes for Windows:

- Use `python -m alembic`, not the `alembic` executable (Application Control blocked the shim).
- psycopg's async mode cannot use Windows' default event loop. `python -m app.api` and
  `python -m app.worker` select the right one; plain `uvicorn` only does with `--reload`.
- The compose database runs with `fsync=off` because durable writes were taking 20+ seconds per
  `CREATE DATABASE` on Docker Desktop. That is for dev and tests only, never production.

## API

All endpoints need an `X-API-Key` header, except `/health`.

| Endpoint | Purpose |
|---|---|
| `POST /audits` | Create an audit. Needs an `Idempotency-Key` header. `202` when created, `200` for a repeat of the same key and body (no new model, search or fetch calls), `422` for an invalid body or a key reused with a different body, `503` if the job could not be queued |
| `GET /audits` | List audits, newest first. Cursor pagination (`limit`, `cursor`), filters `status` and `brand` |
| `GET /audits/{id}` | The audit with its recomputed summary |
| `GET /audits/{id}/runs` | One row per run (question, model, mode, repeat) for the dot grid |
| `GET /runs/{id}` | One run: raw answer, extracted quote and highlight offsets, verdict and findings, citations with their fetch evidence |
| `GET /audits/{id}/sources` | Researched pages; filters `source_type`, `fetch_status`, `domain` |
| `GET /audits/{id}/sources/{source_id}` | One page snapshot with its text and timestamp |
| `GET /audits/{id}/gaps` | Domains cited for competitors but never for the brand, ranked |
| `GET /audits/{id}/events` | Live progress as Server-Sent Events, resumable with `Last-Event-ID` |
| `POST /mentions/{run_id}/review` | `approve`, `correct` or `reject` an extracted mention, with a required reason |
| `GET /review/queue` | Flagged mentions still waiting for a human decision |
| `GET /evals/runs` | Stored eval runs |
| `GET /health` | Liveness |

Progress events are stored in Postgres with a gapless per-audit sequence. A client that reconnects
with `Last-Event-ID: <n>` receives exactly the events after `n`. A finished audit with nothing newer
answers `204`, which tells `EventSource` to stop reconnecting.

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
- **mention_rate**: confirmed mentions over usable runs in the headline search mode (grounded when
  the audit ran `grounded` or `both`, otherwise off). Failed runs are excluded.
- **mode_delta** (only for `both`): grounded mention rate minus off mention rate.
- **avg_position**: mean position over confirmed mentions that have one.
- **stability**: per question, model and search mode, the share of repeats that agree with their own
  majority. It is reported **per group** (`question_stability`) and never averaged away; the
  headline is only the mean of those, and `repeats_compared` says how many groups had two or more
  usable repeats. With one repeat there is nothing to compare.
- **Latency P50/P90**: nearest-rank over the successful model calls.

An audit is `capped` when any limit stopped it (cost cap, 30 fetches, 5 per domain, max steps),
`failed` when no run produced a usable answer, and `complete` otherwise. A provider that is down
fails its own runs, not the audit.

## Evals

See `evals/README.md` for the dataset formats and metric definitions.

```
uv run python -m app.evals run --prompt-version v1 --repeats 3 --out evals/results/v1.json
uv run python -m app.evals run --prompt-version v2 --repeats 3 --out evals/results/v2.json
uv run python -m app.evals compare evals/results/v1.json evals/results/v2.json
```

The output is a markdown table you can paste here. Prompts are versioned files in `prompts/`.

## Safety

- Every outbound page fetch goes through `app/fetch/safe_fetch.py`: http(s) only, DNS resolved first
  with every address checked, the connection pinned to the checked IP, redirects re-checked, robots.txt
  honoured, 10 s timeout, 1 MB decoded-size cap, text content only.
- Fetched text is untrusted data. The only model call on it classifies the source type, with the
  text inside a delimited data block, no tools, no knowledge of the audited brand, and an
  enum-only output.
- The verifier, the page brand check and the gap report are plain code. No model writes them.
- `FetchResult.detail` can name internal addresses and is never returned by the API.

Known gaps are listed in the security review notes: text hidden with external CSS classes or
same-colour text still counts as visible, and a site can serve our bot different content.
