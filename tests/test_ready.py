"""/ready: checks the database and Redis, is separate from /health, never leaks internals."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import psycopg
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.db.session import make_session_factory
from app.worker.arq_queue import ArqQueue
from tests.api_support import ApiHarness, build_harness
from tests.conftest import run_async

Conn = psycopg.Connection[Any]
REDIS_URL = "redis://localhost:6379/15"
SECRET_BITS = ("postgresql", "password", "localhost", "6379", "Traceback", "refused")


class PingQueue:
    def __init__(self, ping: Callable[[], Awaitable[bool]]) -> None:
        self.ping = ping

    async def enqueue(self, audit_id: str) -> None:
        return None


def scenario(url: str, body: Callable[[ApiHarness, httpx.AsyncClient], Awaitable[None]]) -> None:
    async def go() -> None:
        h = build_harness(url)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=h.app), base_url="http://t"
        ) as c:
            try:
                await body(h, c)
            finally:
                await h.close()

    run_async(go)


def test_ready_is_ok_when_db_is_up_and_the_queue_has_no_redis(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        r = await c.get("/ready")  # no API key: probes must work unauthenticated
        assert r.status_code == 200
        assert r.json() == {"status": "ready", "checks": {"db": "ok", "redis": "not_used"}}

    scenario(migrated_db_url, body)


def test_ready_pings_the_real_redis(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        queue = ArqQueue.from_url(REDIS_URL)
        h.app.state.queue = queue
        try:
            r = await c.get("/ready")
        finally:
            await queue.aclose()
        assert r.status_code == 200 and r.json()["checks"] == {"db": "ok", "redis": "ok"}

    scenario(migrated_db_url, body)


def test_redis_down_is_503_and_names_only_the_failed_check(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        queue = ArqQueue.from_url("redis://127.0.0.1:1/0")
        h.app.state.queue = queue
        try:
            r = await c.get("/ready")
        finally:
            await queue.aclose()
        assert r.status_code == 503
        body_ = r.json()
        assert body_["status"] == "unavailable" and body_["checks"]["db"] == "ok"
        # a refused connection reports "failed"; Windows can be slow enough to hit the timeout
        assert body_["checks"]["redis"] in ("failed", "timeout")
        assert not any(bit in r.text for bit in ("127.0.0.1", "refused", "Error"))

    scenario(migrated_db_url, body)


def test_db_down_is_503_and_leaks_no_connection_string() -> None:
    async def go() -> None:
        url = "postgresql+psycopg://user:hunter2secret@127.0.0.1:1/nope"
        engine = create_async_engine(url, poolclass=NullPool)
        h = build_harness(url)
        h.app.state.session_factory = make_session_factory(engine)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=h.app), base_url="http://t"
        ) as c:
            r = await c.get("/ready")
            assert r.status_code == 503
            assert r.json()["checks"]["db"] in ("failed", "timeout")
            assert "hunter2secret" not in r.text and not any(b in r.text for b in SECRET_BITS[:5])
            assert (await c.get("/health")).status_code == 200  # liveness is independent
        await engine.dispose()
        await h.close()

    run_async(go)


def test_a_hanging_dependency_times_out_instead_of_hanging_the_probe(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        async def hang() -> bool:
            await asyncio.sleep(30)
            return True

        h.app.state.queue = PingQueue(hang)
        h.app.state.settings = Settings(ready_timeout_s=0.1, rate_limit_default_per_min=100_000)
        loop = asyncio.get_running_loop()
        started = loop.time()
        r = await c.get("/ready")
        assert loop.time() - started < 2
        assert r.status_code == 503 and r.json()["checks"]["redis"] == "timeout"

    scenario(migrated_db_url, body)


def test_health_stays_a_cheap_liveness_check_even_when_everything_is_down() -> None:
    async def go() -> None:
        h = build_harness("postgresql+psycopg://x:y@127.0.0.1:1/none")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=h.app), base_url="http://t"
        ) as c:
            r = await c.get("/health")
            assert r.status_code == 200 and r.json() == {"status": "ok"}
        await h.close()

    run_async(go)
