"""The queue interface the API depends on, plus a dev-only in-process implementation."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

logger = logging.getLogger("app.worker")


class AuditQueue(Protocol):
    async def enqueue(self, audit_id: str) -> None:
        """Accept a job for `audit_id`. Raises if the job could not be accepted."""
        ...


class InProcessQueue:
    """Runs audits as tasks inside the web process. NOT durable: a restart loses running jobs.

    For local development only. Production uses the ARQ queue served by app/worker.
    """

    def __init__(self, job: Callable[[str], Awaitable[object]]) -> None:
        self._job = job
        self._tasks: set[asyncio.Task[object]] = set()

    async def enqueue(self, audit_id: str) -> None:
        task = asyncio.create_task(self._run(audit_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, audit_id: str) -> object:
        try:
            return await self._job(audit_id)
        except Exception:
            logger.exception("in_process_job_failed", extra={"audit_id": audit_id})
            return None
