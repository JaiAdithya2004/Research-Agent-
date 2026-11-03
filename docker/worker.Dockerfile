# Worker image. Build from the repo root:  docker build -f docker/worker.Dockerfile -t audit-worker .
# Same code and dependencies as the API; only the command differs. The worker is the only
# process that fetches arbitrary URLs, so it is deployed as its own app with its own network policy.
FROM python:3.11-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PIP_NO_CACHE_DIR=1
RUN pip install uv==0.9.5
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/app/.venv/bin:$PATH"
RUN useradd --system --uid 10001 --create-home app
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY prompts ./prompts
USER app
CMD ["python", "-m", "app.worker"]
