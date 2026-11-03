"""Recover audits whose worker died.

ARQ retries jobs that fail or are interrupted by a clean shutdown, but a worker that is killed
outright leaves its job unfinished. Every step an audit takes bumps the audit's updated_at (via
its progress events), so a `running` audit that has been silent for longer than the job timeout
has lost its worker. Re-enqueueing it is safe: the audit job is idempotent per step, and the
deterministic job id stops a duplicate while the original is still alive.
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Audit
from app.worker.queue import AuditQueue

logger = logging.getLogger("app.worker")

JOB_TIMEOUT = timedelta(minutes=30)
STALE_AFTER = JOB_TIMEOUT + timedelta(minutes=5)


async def find_stale_audits(
    session_factory: async_sessionmaker[AsyncSession],
    older_than: timedelta = STALE_AFTER,
    now: datetime | None = None,
) -> list[str]:
    cutoff = (now or datetime.now(UTC)) - older_than
    async with session_factory() as session:
        rows = await session.execute(
            select(Audit.id)
            .where(Audit.status == "running", Audit.updated_at < cutoff)
            .order_by(Audit.updated_at)
        )
        return [str(i) for i in rows.scalars()]


async def requeue_stale(
    session_factory: async_sessionmaker[AsyncSession],
    queue: AuditQueue,
    older_than: timedelta = STALE_AFTER,
    now: datetime | None = None,
) -> list[str]:
    requeued: list[str] = []
    for audit_id in await find_stale_audits(session_factory, older_than, now):
        try:
            await queue.enqueue(audit_id)
        except Exception:
            logger.exception("requeue_failed", extra={"audit_id": audit_id})
            continue
        requeued.append(audit_id)
    if requeued:
        logger.warning("requeued_stale_audits", extra={"count": len(requeued)})
    return requeued
