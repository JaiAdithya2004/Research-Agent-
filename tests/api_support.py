"""Shared harness for API tests: the real app and Postgres, with fake models, fetcher and queue."""

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.main import create_app
from app.audit.events import EventSink
from app.audit.pg_events import PgEvents
from app.audit.runner import AuditConfig, ModelTarget
from app.config import Settings
from app.db.session import make_session_factory
from app.llm.cost import CostTracker
from app.worker.jobs import Runtime, execute_audit
from tests.test_orchestrator import FakeClient
from tests.test_researcher import FakeFetcher


class TestQueue:
    """A queue that runs jobs in-process, with controls to hold, release and fail them."""

    __test__ = False

    def __init__(self, job: Callable[[str], Coroutine[Any, Any, object]]) -> None:
        self._job = job
        self.enqueued: list[str] = []
        self._held = False
        self._pending: list[str] = []
        self._tasks: list[asyncio.Task[object]] = []
        self._fail = 0

    def hold(self) -> None:
        self._held = True

    def release(self) -> None:
        self._held = False
        for audit_id in self._pending:
            self._start(audit_id)
        self._pending.clear()

    def fail_next(self, n: int = 1) -> None:
        self._fail = n

    def _start(self, audit_id: str) -> None:
        self._tasks.append(asyncio.create_task(self._job(audit_id)))

    async def enqueue(self, audit_id: str) -> None:
        if self._fail > 0:
            self._fail -= 1
            raise ConnectionError("queue unavailable")
        self.enqueued.append(audit_id)
        if self._held:
            self._pending.append(audit_id)
        else:
            self._start(audit_id)

    async def drain(self) -> None:
        for task in list(self._tasks):
            await task


class _Tracker:
    def __init__(self, clients: list[FakeClient]) -> None:
        self._clients = clients

    @property
    def spent_usd(self) -> float:
        return sum(c.tracker.spent_usd for c in self._clients)


class LlmView:
    """Counts across every per-audit fake client the job factory created."""

    def __init__(self) -> None:
        self.clients: list[FakeClient] = []

    @property
    def calls(self) -> list[tuple[str, str, bool]]:
        return [call for c in self.clients for call in c.calls]

    @property
    def tracker(self) -> _Tracker:
        return _Tracker(self.clients)


@dataclass
class ApiHarness:
    app: FastAPI
    session_factory: async_sessionmaker[AsyncSession]
    queue: TestQueue
    llm: LlmView
    fetcher: FakeFetcher
    events: EventSink
    _dispose: Callable[[], Awaitable[None]] = field(repr=False, default=lambda: asyncio.sleep(0))

    async def close(self) -> None:
        await self.queue.drain()
        await self._dispose()


def build_harness(database_url: str, *, api_key: str = "test-key", **client_kw: Any) -> ApiHarness:
    engine = create_async_engine(database_url, poolclass=NullPool)
    session_factory = make_session_factory(engine)
    llm = LlmView()
    fetcher = FakeFetcher()
    events = PgEvents(session_factory)

    def make_llm(tracker: CostTracker) -> FakeClient:
        client = FakeClient(tracker, cost=0.01, **client_kw)
        llm.clients.append(client)
        return client

    runtime = Runtime(
        session_factory=session_factory,
        make_llm=make_llm,
        make_fetcher=lambda store: fetcher,
        config=AuditConfig(
            targets=[ModelTarget("gemini", "g")], extractor=ModelTarget("gemini", "g")
        ),
        events=events,
    )
    queue = TestQueue(lambda audit_id: execute_audit(audit_id, runtime))
    app = create_app(
        settings=Settings(api_key=api_key), session_factory=session_factory, queue=queue
    )
    return ApiHarness(
        app=app,
        session_factory=session_factory,
        queue=queue,
        llm=llm,
        fetcher=fetcher,
        events=events,
        _dispose=engine.dispose,
    )
