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
