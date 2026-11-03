"""The ARQ worker, retried jobs and the stale-audit sweeper.

Needs Postgres and Redis (`docker compose up -d --wait`). Redis database 15 is used and flushed.
"""

import asyncio
import os
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import timedelta
from typing import Any

import httpx
import psycopg
import pytest
from arq.connections import RedisSettings
from arq.worker import Worker
from redis.asyncio import Redis
from sqlalchemy import func, select, text

import app.worker.arq_worker as arq_worker
from app.audit.pg_events import PgEvents, fetch_events
from app.audit.runner import AuditConfig, ModelTarget
from app.db.models import Audit, Run
from app.llm.cost import CostTracker
from app.worker.arq_queue import JOB_NAME, ArqQueue, job_id
from app.worker.arq_worker import run_audit_job
from app.worker.jobs import Runtime
from app.worker.sweeper import find_stale_audits, requeue_stale
from tests.api_support import ApiHarness, LlmView, build_harness
from tests.conftest import run_async
from tests.test_orchestrator import QUESTIONS, Crash, FakeClient

Conn = psycopg.Connection[Any]
REDIS_URL = f"redis://localhost:6379/{os.environ.get('TEST_REDIS_DB', '15')}"
KEY = {"X-API-Key": "test-key"}
BODY: dict[str, Any] = {
    "brand": "Acme",
    "category": "crm",
    "competitors": ["Rival"],
    "questions": QUESTIONS,
    "repeats": 2,
    "search_mode": "grounded",
}


@pytest.fixture(autouse=True)
def clean_redis() -> Iterator[None]:
    async def flush() -> None:
        r = Redis.from_url(REDIS_URL)
        try:
            await r.flushdb()
        finally:
            await r.aclose()

    run_async(flush)
    yield
    run_async(flush)


async def create_audit(h: ApiHarness, key: str = "k") -> str:
    async with h.session_factory() as s, s.begin():
        audit = Audit(
            idempotency_key=f"{key}-{uuid.uuid4()}", request=BODY, brand="Acme", cost_cap_usd=5
        )
        s.add(audit)
        await s.flush()
        return str(audit.id)


def make_runtime(h: ApiHarness, *, make_llm: Callable[[CostTracker], Any] | None = None) -> Runtime:
    clients = h.llm.clients

    def default_llm(tracker: CostTracker) -> FakeClient:
        client = FakeClient(tracker, cost=0.01)
        clients.append(client)
        return client

    return Runtime(
        session_factory=h.session_factory,
        make_llm=make_llm or default_llm,
        make_fetcher=lambda store: h.fetcher,
        config=AuditConfig(
            targets=[ModelTarget("gemini", "g")], extractor=ModelTarget("gemini", "g")
        ),
        events=PgEvents(h.session_factory),
    )


def scenario(url: str, body: Callable[[ApiHarness], Awaitable[None]]) -> None:
    async def go() -> None:
        h = build_harness(url)
        try:
            await body(h)
        finally:
            await h.close()

    run_async(go)


async def run_worker_burst(runtime: Runtime) -> None:
    worker = Worker(
        functions=[run_audit_job],
        redis_settings=RedisSettings.from_dsn(REDIS_URL),
        ctx={"runtime": runtime},
        burst=True,
        max_tries=3,
        keep_result=0,
        retry_jobs=True,
        handle_signals=False,
        poll_delay=0.05,
    )
    try:
        await worker.async_run()
    finally:
        # Worker.close() uses signal.SIGUSR1, which does not exist on Windows; closing the pool
        # directly is all a burst-mode test needs.
        await worker.pool.aclose()


async def audit_row(h: ApiHarness, audit_id: str) -> Audit:
    async with h.session_factory() as s:
        audit = await s.get(Audit, uuid.UUID(audit_id))
        assert audit is not None
        return audit


async def n_runs(h: ApiHarness) -> int:
    async with h.session_factory() as s:
        return int((await s.execute(select(func.count()).select_from(Run))).scalar_one())


# --- a retried job does not repeat work or spend ---------------------------------------------------------------


def test_a_job_that_died_mid_audit_resumes_without_repeating_work(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness) -> None:
        from app.worker.jobs import execute_audit

        aid = await create_audit(h)
        first = make_runtime(h)
        client_calls: list[FakeClient] = []

        def dying_llm(tracker: CostTracker) -> FakeClient:
            client = FakeClient(tracker, cost=0.01)
            client_calls.append(client)
            original = client.generate
            state = {"answers": 0}

            async def generate(**kw: Any) -> Any:
                if "You extract" not in kw["prompt"] and "You classify" not in kw["prompt"]:
                    state["answers"] += 1
                    if state["answers"] == 3:
                        raise Crash  # the worker is killed during the third answer
                return await original(**kw)

            client.generate = generate  # type: ignore[method-assign]
            return client

        first.make_llm = dying_llm
        with pytest.raises(Crash):
            await execute_audit(aid, first)
        done_before = await n_runs(h)
        assert 0 < done_before < 4

        second = make_runtime(h)  # the retry runs in a fresh process
        outcome = await execute_audit(aid, second)
        answers_first = sum(c.count("answer") for c in client_calls)
        answers_second = sum(c.count("answer") for c in h.llm.clients)
        assert outcome.status == "complete"
        # 2 answers before the crash, 2 after: each of the 4 steps was asked exactly once.
        assert answers_first == 2 and answers_first + answers_second == 4
        assert await n_runs(h) == 4  # no duplicate runs
        row = await audit_row(h, aid)
        assert float(row.total_cost_usd) == pytest.approx(outcome.summary.total_cost_usd)

    scenario(migrated_db_url, body)


def test_a_resumed_audit_says_so_and_does_not_repeat_finished_steps_in_the_event_log(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness) -> None:
        from app.worker.jobs import execute_audit

        aid = await create_audit(h)
        await execute_audit(aid, make_runtime(h))
        await execute_audit(aid, make_runtime(h))  # a spurious second run of a finished audit
        events = await fetch_events(h.session_factory, uuid.UUID(aid), 0, limit=500)
        types = [e.type for e in events]
        assert types.count("audit_started") == 1 and types.count("audit_resumed") == 1
        assert types.count("run_started") == 4  # not 8: finished steps are not re-announced
        assert types.count("audit_completed") == 2 and types[-1] == "audit_completed"
        assert [e.seq for e in events] == list(range(1, len(events) + 1))

    scenario(migrated_db_url, body)


def test_a_second_run_of_a_finished_audit_spends_nothing(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness) -> None:
        from app.worker.jobs import execute_audit

        aid = await create_audit(h)
        await execute_audit(aid, make_runtime(h))
        calls, fetches = len(h.llm.calls), len(h.fetcher.calls)
        await execute_audit(aid, make_runtime(h))
        assert (len(h.llm.calls), len(h.fetcher.calls)) == (calls, fetches)

    scenario(migrated_db_url, body)


# --- ARQ ---------------------------------------------------------------------------------------------------------------


def test_enqueueing_the_same_audit_twice_queues_one_job(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness) -> None:
        queue = ArqQueue.from_url(REDIS_URL)
        try:
            for _ in range(3):
                await queue.enqueue("abc")
            pool = await queue._redis()
            jobs = await pool.queued_jobs()
            assert [j.job_id for j in jobs] == [job_id("abc")] and jobs[0].function == JOB_NAME
            assert await queue.ping() is True
        finally:
            await queue.aclose()

    scenario(migrated_db_url, body)


def test_the_worker_runs_a_queued_audit_to_completion(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness) -> None:
        aid = await create_audit(h)
        queue = ArqQueue.from_url(REDIS_URL)
        await queue.enqueue(aid)
        await queue.aclose()
        await run_worker_burst(make_runtime(h))
        assert (await audit_row(h, aid)).status == "complete"
        assert await n_runs(h) == 4
        types = [
            e.type for e in await fetch_events(h.session_factory, uuid.UUID(aid), 0, limit=500)
        ]
        assert types[0] == "audit_started" and types[-1] == "audit_completed"

    scenario(migrated_db_url, body)


def test_a_failing_job_is_retried_and_then_succeeds_without_duplicate_work(
    migrated_db_url: str, db: Conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(arq_worker, "retry_delay", lambda tries: 0.05)

    async def body(h: ApiHarness) -> None:
        aid = await create_audit(h)
        attempts = {"n": 0}

        def flaky_llm(tracker: CostTracker) -> FakeClient:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise ConnectionError("database blip")  # the whole job fails before doing any work
            client = FakeClient(tracker, cost=0.01)
            h.llm.clients.append(client)
            return client

        queue = ArqQueue.from_url(REDIS_URL)
        await queue.enqueue(aid)
        await queue.aclose()
        await run_worker_burst(make_runtime(h, make_llm=flaky_llm))
        assert attempts["n"] == 2
        assert (await audit_row(h, aid)).status == "complete"
        assert await n_runs(h) == 4 and h.llm.clients[0].count("answer") == 4

    scenario(migrated_db_url, body)


def test_a_job_that_keeps_failing_marks_the_audit_failed_instead_of_leaving_it_running(
    migrated_db_url: str, db: Conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(arq_worker, "retry_delay", lambda tries: 0.02)

    async def body(h: ApiHarness) -> None:
        aid = await create_audit(h)

        def broken(tracker: CostTracker) -> Any:
            raise ConnectionError("always down")

        queue = ArqQueue.from_url(REDIS_URL)
        await queue.enqueue(aid)
        await queue.aclose()
        await run_worker_burst(make_runtime(h, make_llm=broken))
        assert (await audit_row(h, aid)).status == "failed"
        events = await fetch_events(h.session_factory, uuid.UUID(aid), 0)
        assert [e.type for e in events] == ["audit_failed"]

    scenario(migrated_db_url, body)


def test_a_job_for_a_missing_audit_is_dropped_not_retried(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness) -> None:
        queue = ArqQueue.from_url(REDIS_URL)
        await queue.enqueue(str(uuid.uuid4()))
        await queue.aclose()
        await asyncio.wait_for(
            run_worker_burst(make_runtime(h)), timeout=20
        )  # returns, no retry loop

    scenario(migrated_db_url, body)


def test_posting_an_audit_with_the_arq_queue_enqueues_exactly_one_job(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness) -> None:
        from app.api.main import create_app
        from app.config import Settings

        queue = ArqQueue.from_url(REDIS_URL)
        app = create_app(
            settings=Settings(api_key="test-key"), session_factory=h.session_factory, queue=queue
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            headers = {**KEY, "Idempotency-Key": "arq-1"}
            first = await c.post("/audits", json=BODY, headers=headers)
            again = await c.post("/audits", json=BODY, headers=headers)
            assert (first.status_code, again.status_code) == (202, 200)
            jobs = await (await queue._redis()).queued_jobs()
            assert [j.job_id for j in jobs] == [job_id(first.json()["id"])]
            # POST only enqueues: nothing ran in the web process.
            assert await n_runs(h) == 0
        await queue.aclose()

    scenario(migrated_db_url, body)


# --- the sweeper -----------------------------------------------------------------------------------------------------------


class RecordingQueue:
    def __init__(self, fail_for: set[str] | None = None) -> None:
        self.ids: list[str] = []
        self.fail_for = fail_for or set()

    async def enqueue(self, audit_id: str) -> None:
        if audit_id in self.fail_for:
            raise ConnectionError("redis down")
        self.ids.append(audit_id)


async def age(h: ApiHarness, audit_id: str, minutes: int) -> None:
    async with h.session_factory() as s, s.begin():
        await s.execute(
            text("UPDATE audits SET updated_at = now() - make_interval(mins => :m) WHERE id = :id"),
            {"m": minutes, "id": uuid.UUID(audit_id)},
        )


def test_only_stale_running_audits_are_found(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness) -> None:
        stale_id = await create_audit(h, "stale")
        fresh_id = await create_audit(h, "fresh")
        done_id = await create_audit(h, "done")
        capped_id = await create_audit(h, "capped")
        for audit_id, minutes in ((stale_id, 90), (fresh_id, 5), (done_id, 90), (capped_id, 90)):
            await age(h, audit_id, minutes)
        # Raw SQL, so updated_at stays old: the status filter alone must exclude these.
        async with h.session_factory() as s, s.begin():
            await s.execute(
                text("UPDATE audits SET status = 'complete' WHERE id = :id"),
                {"id": uuid.UUID(done_id)},
            )
            await s.execute(
                text("UPDATE audits SET status = 'capped' WHERE id = :id"),
                {"id": uuid.UUID(capped_id)},
            )
        found = await find_stale_audits(h.session_factory, timedelta(minutes=35))
        assert found == [stale_id]

    scenario(migrated_db_url, body)


def test_requeue_sends_each_stale_audit_once_and_survives_a_queue_error(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness) -> None:
        a, b = await create_audit(h, "a"), await create_audit(h, "b")
        await age(h, a, 90)
        await age(h, b, 80)
        queue = RecordingQueue(fail_for={a})
        done = await requeue_stale(h.session_factory, queue, timedelta(minutes=35))
        assert queue.ids == [b] and done == [b]  # a failed, b still went through

    scenario(migrated_db_url, body)


def test_a_worker_that_was_killed_is_recovered_by_the_sweeper(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness) -> None:
        from app.worker.jobs import execute_audit

        aid = await create_audit(h)
        crashing = make_runtime(h)

        def dying(tracker: CostTracker) -> FakeClient:
            client = FakeClient(tracker, cost=0.01)
            original = client.generate

            async def generate(**kw: Any) -> Any:
                if "You extract" in kw["prompt"]:
                    raise Crash
                return await original(**kw)

            client.generate = generate  # type: ignore[method-assign]
            return client

        crashing.make_llm = dying
        with pytest.raises(Crash):
            await execute_audit(aid, crashing)
        assert (await audit_row(h, aid)).status == "running"  # the audit is orphaned
        await age(h, aid, 90)

        queue = ArqQueue.from_url(REDIS_URL)
        try:
            assert await requeue_stale(h.session_factory, queue, timedelta(minutes=35)) == [aid]
            assert await requeue_stale(h.session_factory, queue, timedelta(minutes=35)) == [aid]
            assert (
                len(await (await queue._redis()).queued_jobs()) == 1
            )  # the second sweep was a no-op
        finally:
            await queue.aclose()
        await run_worker_burst(make_runtime(h))
        assert (await audit_row(h, aid)).status == "complete" and await n_runs(h) == 4

    scenario(migrated_db_url, body)


def test_llm_view_is_importable_for_other_tests() -> None:
    assert LlmView is not None


def test_a_job_for_a_missing_audit_reports_missing_and_does_not_raise(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness) -> None:
        result = await run_audit_job({"runtime": make_runtime(h), "job_try": 1}, str(uuid.uuid4()))
        assert result == "missing"

    scenario(migrated_db_url, body)
