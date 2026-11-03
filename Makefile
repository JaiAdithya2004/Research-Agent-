.PHONY: test run lint format typecheck check db-up db-down db-migrate

test:
	uv run pytest

run:
	uv run uvicorn app.api.main:app --reload

lint:
	uv run ruff check .

format:
	uv run ruff format .

typecheck:
	uv run mypy

check: lint typecheck test

db-up:
	docker compose up -d --wait

db-down:
	docker compose down

db-migrate:
	uv run python -m alembic upgrade head
