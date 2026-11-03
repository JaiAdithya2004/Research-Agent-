from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.routes import router
from app.api.routes_review import router as review_router
from app.audit.pg_events import PgEvents
from app.config import Settings
from app.db.session import make_engine, make_session_factory
from app.worker.arq_queue import ArqQueue
from app.worker.jobs import execute_audit
from app.worker.queue import AuditQueue, InProcessQueue
from app.worker.runtime import build_runtime


class HealthResponse(BaseModel):
    status: str


def create_app(
    *,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    queue: AuditQueue | None = None,
) -> FastAPI:
    """Application factory. Tests inject a session factory and a queue; production uses the env."""
    settings = settings or Settings.from_env()
    engine: AsyncEngine | None = None
    if session_factory is None:
        engine = make_engine(settings.database_url)
        session_factory = make_session_factory(engine)
    factory = session_factory

    if queue is None:
        if settings.queue_backend == "inprocess":
            runtime = build_runtime(settings, factory, PgEvents(factory))

            async def job(audit_id: str) -> object:
                return await execute_audit(audit_id, runtime)

            queue = InProcessQueue(job)  # local dev only: not durable, lost on restart
        else:
            queue = ArqQueue.from_url(settings.redis_url)  # production: the worker runs the job

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        yield
        closer = getattr(queue, "aclose", None)
        if closer is not None:
            await closer()
        if engine is not None:
            await engine.dispose()

    app = FastAPI(title="AI Visibility Audit Agent", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.session_factory = factory
    app.state.queue = queue
    app.include_router(router)
    app.include_router(review_router)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok")

    return app


app = create_app()
