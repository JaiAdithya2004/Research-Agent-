# API image. Build from the repo root:  docker build -f docker/api.Dockerfile -t audit-api .
FROM python:3.11-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PIP_NO_CACHE_DIR=1
RUN pip install uv==0.9.5
WORKDIR /app
COPY pyproject.toml uv.lock ./
# Runtime dependencies only (the "dev" extra is not installed), exactly as locked.
RUN uv sync --frozen --no-install-project

FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/app/.venv/bin:$PATH" HOST=0.0.0.0 PORT=8000
RUN useradd --system --uid 10001 --create-home app
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY prompts ./prompts
USER app
EXPOSE 8000
# Migrations are NOT run here: they are a release step (see docs/deploy-fly.md and the README).
CMD ["python", "-m", "app.api"]
