"""The audit job: load the audit, build its dependencies, run the orchestrator."""

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.events import EventSink
from app.audit.pg_repo import PgAuditRepo
from app.audit.runner import AuditConfig, AuditDeps, AuditOutcome, run_audit
from app.db.models import Audit
from app.extract.extractor import Generator
from app.fetch.db_store import DbSnapshotStore
from app.fetch.safe_fetch import SnapshotStore
from app.llm.cost import CostTracker
from app.research.classifier import SourceClassifier
from app.research.researcher import Fetcher
from app.schemas import AuditRequest


@dataclass
class Runtime:
    """Everything an audit job needs from the outside world, injectable for tests."""

    session_factory: async_sessionmaker[AsyncSession]
    make_llm: Callable[[CostTracker], Generator]
    make_fetcher: Callable[[SnapshotStore], Fetcher]
    config: AuditConfig
    events: EventSink


async def execute_audit(audit_id: str, runtime: Runtime) -> AuditOutcome:
    async with runtime.session_factory() as session:
        audit = await session.get(Audit, uuid.UUID(audit_id))
        if audit is None:
            raise LookupError(f"audit {audit_id} does not exist")
        request = AuditRequest.model_validate(audit.request)
        cap = float(audit.cost_cap_usd)

    tracker = CostTracker(cap_usd=cap)
    llm = runtime.make_llm(tracker)
    cfg = runtime.config
    deps = AuditDeps(
        llm=llm,
        tracker=tracker,
        fetcher=runtime.make_fetcher(DbSnapshotStore(runtime.session_factory)),
        classifier=SourceClassifier(
            llm, provider=cfg.extractor.provider, model=cfg.extractor.model
        ),
        repo=PgAuditRepo(runtime.session_factory),
        events=runtime.events,
        config=cfg,
    )
    return await run_audit(audit_id, request, deps)
