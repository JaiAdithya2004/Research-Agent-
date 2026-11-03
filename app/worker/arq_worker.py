"""The ARQ worker: runs audits outside the web process.

uv run arq app.worker.arq_worker.WorkerSettings        (Linux / Fly)
uv run python -m app.worker                            (Windows-safe launcher)
"""

import asyncio
import logging
import os
import sys
from typing import Any

from arq import cron
from arq.connections import RedisSettings
from arq.worker import Retry

from app.audit.pg_events import PgEvents
from app.audit.pg_repo import PgAuditRepo
from app.audit.records import AuditState
from app.config import Settings
from app.db.session import make_engine, make_session_factory
from app.logging_config import configure_logging
from app.observability import build_tracer, get_tracer, init_sentry, set_tracer
from app.worker.arq_queue import ArqQueue
from app.worker.jobs import Runtime, execute_audit
from app.worker.runtime import build_runtime
from app.worker.sweeper import JOB_TIMEOUT, requeue_stale

logger = logging.getLogger("app.worker")

if sys.platform == "win32":  # psycopg's async mode cannot use the default Proactor loop
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

MAX_TRIES = 3


def retry_delay(tries: int) -> float:
    """Seconds to wait before the next attempt: 10s, 20s, ... capped at a minute."""
    return float(min(60, 5 * 2**tries))


async def mark_failed(runtime: Runtime, audit_id: str) -> None:
    """The job is out of retries: say so, once, instead of leaving the audit 'running' forever."""
    repo = PgAuditRepo(runtime.session_factory)
    state = await repo.load_state(audit_id)
    await repo.save_state(audit_id, AuditState(**{**state.model_dump(), "status": "failed"}))
    await runtime.events.emit(audit_id, "audit_failed", {"reason": "job failed after retries"})


async def run_audit_job(ctx: dict[str, Any], audit_id: str) -> str:
    runtime: Runtime = ctx["runtime"]
    try:
        outcome = await execute_audit(audit_id, runtime)
    except LookupError:
        logger.error("audit_missing", extra={"audit_id": audit_id})
        return "missing"  # nothing to retry: the audit row is gone
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        tries = int(ctx.get("job_try", 1))
        logger.warning(
            "audit_job_error",
            extra={"audit_id": audit_id, "try": tries, "error": type(exc).__name__},
        )
        if tries >= MAX_TRIES:
            await mark_failed(runtime, audit_id)
            raise
        raise Retry(defer=retry_delay(tries)) from exc
    return outcome.status


async def startup(ctx: dict[str, Any]) -> None:
    settings = Settings.from_env()
    configure_logging()
    init_sentry(settings.sentry_dsn, environment=settings.environment)
    set_tracer(build_tracer())
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    ctx["engine"] = engine
    ctx["settings"] = settings
    ctx["runtime"] = build_runtime(settings, factory, PgEvents(factory))


async def shutdown(ctx: dict[str, Any]) -> None:
    get_tracer().flush()
    await ctx["engine"].dispose()


async def sweep_stale_audits(ctx: dict[str, Any]) -> int:
    runtime: Runtime = ctx["runtime"]
    queue = ArqQueue(RedisSettings.from_dsn(ctx["settings"].redis_url))
    try:
        return len(await requeue_stale(runtime.session_factory, queue))
    finally:
        await queue.aclose()


class WorkerSettings:
    functions = [run_audit_job]
    cron_jobs = [cron(sweep_stale_audits, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55})]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(Settings.from_env().redis_url)
    max_tries = MAX_TRIES
    job_timeout = int(JOB_TIMEOUT.total_seconds())
    keep_result = 0  # a finished job must not block a later re-enqueue of the same audit
    max_jobs = 4
    # ARQ polls Redis every poll_delay seconds even when idle. Managed Redis free tiers count
    # commands, so poll slowly; set WORKER_POLL_DELAY_S=0.5 for snappier starts on your own Redis.
    poll_delay = float(os.environ.get("WORKER_POLL_DELAY_S", "5"))
    retry_jobs = True
