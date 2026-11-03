"""The production queue: ARQ on Redis.

Enqueueing uses a deterministic job id, so enqueueing the same audit twice is a no-op: ARQ refuses
a job id that is already queued or running. That is what makes POST retries and the stale-audit
sweeper safe.
"""

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

JOB_NAME = "run_audit_job"


def job_id(audit_id: str) -> str:
    return f"audit-{audit_id}"


class ArqQueue:
    def __init__(self, redis_settings: RedisSettings) -> None:
        self._settings = redis_settings
        self._pool: ArqRedis | None = None

    @classmethod
    def from_url(cls, url: str) -> "ArqQueue":
        return cls(RedisSettings.from_dsn(url))

    async def _redis(self) -> ArqRedis:
        if self._pool is None:
            self._pool = await create_pool(self._settings)
        return self._pool

    async def enqueue(self, audit_id: str) -> None:
        pool = await self._redis()
        # Returns None when a job with this id already exists: that is success, not failure.
        await pool.enqueue_job(JOB_NAME, audit_id, _job_id=job_id(audit_id))

    async def ping(self) -> bool:
        pool = await self._redis()
        return bool(await pool.ping())

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.aclose()
            self._pool = None
