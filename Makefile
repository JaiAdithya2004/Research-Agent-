.PHONY: test run lint format typecheck check

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
