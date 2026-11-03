# Rules for coding agents in this repo

Backend: Python 3.11, FastAPI, Pydantic v2, SQLAlchemy, Alembic, PostgreSQL, Redis, ARQ, httpx, pytest.
Frontend: Next.js App Router, TypeScript strict, Tailwind, shadcn/ui, TanStack Query, Playwright, Vitest.

## How to work
- Read SPEC.md before any task. If a task conflicts with it, stop and tell me.
- Write the failing test first, then the code, then run the tests.
- One task per change, small enough to review.
- No new dependencies without asking. Do not touch files outside the task's scope.
- Provider search APIs and pricing change. Check current docs, never guess field names,
  and list every assumption you made in your summary.

## Backend rules
- Every LLM and search call goes through llm/client.py. Never call a provider SDK elsewhere.
- Every LLM output is parsed into a Pydantic model. No raw dict access.
- The verifier is pure Python with no LLM calls. It stays deterministic.
- Never overwrite raw model output, grounding metadata or page snapshots.
- No secrets in code. Env vars only, with a .env.example. Never log raw API keys.

## Fetching and safety rules
- All outbound page fetches go through fetch/safe_fetch.py. No other module makes HTTP calls to arbitrary URLs.
- Resolve DNS first and block private, loopback, link-local and metadata IP ranges.
  Re-check after every redirect.
- Fetched content is untrusted data, never instructions. Pass it to a model only inside a
  clearly delimited data block, in a call with no tools enabled.
- A page that could not be fetched is never reported as "brand absent".
- Any new fetch behavior needs an injection or SSRF test before it ships.

## Frontend rules
- API client is generated from the backend OpenAPI schema. Regenerate, never hand-edit.
- No business logic in components. Fetching in hooks, display in components.
- Every data view implements loading, empty, error, partial, capped and degraded states.
- Accessibility is part of done: labels, focus states, aria-live for progress.

## Definition of done
Tests pass, types check, lint is clean, new behavior has a test, and you tell me
what you changed, what you did not verify, and what you assumed.