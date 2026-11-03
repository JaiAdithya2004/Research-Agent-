# AI Visibility Audit Agent

See SPEC.md for the spec and AGENTS.md for working rules.

Database access uses async SQLAlchemy 2.0 with the psycopg 3 driver (`postgresql+psycopg://`).

## Setup

```
uv sync --extra dev
copy .env.example .env
```

## Commands (Windows, no make)

Each `make` target has a direct equivalent:

| make target      | command                                      |
|------------------|----------------------------------------------|
| `make test`      | `uv run pytest`                              |
| `make run`       | `uv run uvicorn app.api.main:app --reload`   |
| `make lint`      | `uv run ruff check .`                        |
| `make format`    | `uv run ruff format .`                       |
| `make typecheck` | `uv run mypy`                                |
| `make check`     | run lint, typecheck, then test, in that order |

## Database

Postgres runs in Docker. DB tests need it up (they create and migrate a separate `audit_test`
database, so your dev data is never touched).

| make target       | command                                   |
|-------------------|-------------------------------------------|
| `make db-up`      | `docker compose up -d --wait`             |
| `make db-down`    | `docker compose down`                     |
| `make db-migrate` | `uv run python -m alembic upgrade head`   |

Use `python -m alembic`, not the `alembic` executable: Windows Application Control blocked the
`alembic.exe` shim on this machine.

Raw answers, grounding metadata, page snapshots and reviews are protected by database triggers:
`runs.raw_answer` and `runs.raw_grounding` cannot change once written; `page_snapshots` and
`reviews` rows cannot be updated or deleted.
