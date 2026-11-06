"""AuditRepo contract: the same assertions run against the in-memory and Postgres repos.

The last tests run the whole orchestrator on Postgres and check the stored rows.
"""

import uuid
from collections.abc import Iterator
from typing import Any, Literal

import psycopg
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.audit.pg_repo import PgAuditRepo
from app.audit.records import AuditState, RunRecord, StepKey
from app.audit.repo import AuditRepo, InMemoryAuditRepo
from app.db.models import Audit
from app.db.models import Citation as CitationRow
from app.db.models import Review as ReviewRow
from app.db.models import Run as RunRow
from app.db.models import SourceEvidence as EvidenceRow
from app.db.session import make_session_factory
from app.research.researcher import ResearchedPage
from app.schemas import Citation, Mention, SourceEvidence, Verdict
from app.verify.verifier import VerificationReport
from tests.conftest import run_async
from tests.test_orchestrator import QUESTIONS, Harness, harness, request
from tests.test_safe_fetch import T0

Conn = psycopg.Connection[Any]


def sf_for(url: str) -> async_sessionmaker[AsyncSession]:
    # NullPool: every run_async() call uses its own event loop, so connections must not be shared.
    from sqlalchemy.ext.asyncio import create_async_engine

    return make_session_factory(create_async_engine(url, poolclass=NullPool))


@pytest.fixture(params=["memory", "postgres"])
def repo_and_id(
    request: pytest.FixtureRequest, migrated_db_url: str, db: Conn
) -> Iterator[tuple[AuditRepo, str]]:
    if request.param == "memory":
        yield InMemoryAuditRepo(), "audit-mem"
        return
    sf = sf_for(migrated_db_url)

    async def create() -> str:
        async with sf() as session, session.begin():
            audit = Audit(
                idempotency_key=f"k-{uuid.uuid4()}", request={"brand": "Acme"}, brand="Acme"
            )
            session.add(audit)
            await session.flush()
            return str(audit.id)

    yield PgAuditRepo(sf), run_async(create)


def key(
    q: int = 0,
    repeat: int = 0,
    mode: Literal["off", "grounded", "tavily"] = "grounded",
    provider: str = "gemini",
) -> StepKey:
    return StepKey(
        question_idx=q, provider=provider, model="g", repeat_idx=repeat, search_mode=mode
    )


def cite(
    url: str, source: Literal["grounding_metadata", "answer_text"] = "grounding_metadata"
) -> Citation:
    return Citation(
        url=url, domain=url.split("//")[1].split("/")[0], title="T", source=source, retrieved_at=T0
    )


def full_record(k: StepKey | None = None) -> RunRecord:
    k = k or key()
    mention = Mention(
        brand_mentioned=True,
        position=1,
        quote="Acme rocks",
        competitors_seen=["Rival"],
        citations=[cite("https://a.example/x")],
        sentiment="pos",
    )
    verdict = Verdict(status="verified", reason="quote found")
    return RunRecord(
        id=str(uuid.uuid4()),
        key=k,
        question="Best CRM?",
        status="ok",
        raw_answer="Acme rocks. Rival too.",
        raw_grounding={"steps": [1, 2], "nested": {"a": None}},
        citations=[cite("https://a.example/x"), cite("https://blog.example/p", "answer_text")],
        mention=mention,
        verdict=verdict,
        verification=VerificationReport(
            verdict=verdict, findings=[], prose_citations=[], computed_position=1
        ),
        extractor_version="v1",
        latency_ms=321,
        cost_usd=0.0123,
        degraded=True,
        search_calls=2,
        input_tokens=11,
        output_tokens=7,
    )


def page(url: str = "https://a.example/x", **kw: Any) -> ResearchedPage:
    data: dict[str, Any] = {
        "evidence": SourceEvidence(
            url=url,
            final_url=url,
            fetch_status="ok",
            brand_on_page=True,
            quote="Acme rocks.",
            source_type="review",
            snapshot_id=None,
        ),
        "domain": "a.example",
        "cited_domain": "a.example",
        "fetch_reason": "ok",
        "from_cache": False,
        "classification": "model",
        "classification_reason": "classified by the model",
        "brand_check_reason": "brand found",
    }
    data.update(kw)
    return ResearchedPage(**data)


def test_run_roundtrip_keeps_every_field(repo_and_id: tuple[AuditRepo, str]) -> None:
    repo, aid = repo_and_id
    record = full_record()
    run_async(lambda: repo.save_run(aid, record))
    got = run_async(lambda: repo.get_run(aid, record.key))
    assert got is not None
    assert got.model_dump(mode="json", exclude={"citations"}) == record.model_dump(
        mode="json", exclude={"citations"}
    )
    assert [(c.url, c.source) for c in got.citations] == [
        (c.url, c.source) for c in record.citations
    ]


def test_missing_run_is_none_and_unknown_audit_is_empty(repo_and_id: tuple[AuditRepo, str]) -> None:
    repo, aid = repo_and_id
    assert run_async(lambda: repo.get_run(aid, key())) is None
    assert run_async(lambda: repo.list_runs(aid)) == []
    assert run_async(lambda: repo.load_evidence(aid)) == []


def test_pending_run_can_be_completed_and_stays_a_single_row(
    repo_and_id: tuple[AuditRepo, str],
) -> None:
    repo, aid = repo_and_id
    pending = RunRecord(
        key=key(), question="Q?", status="pending", raw_answer="raw", raw_grounding={"a": 1}
    )
    run_async(lambda: repo.save_run(aid, pending))
    done = pending.model_copy(update={"status": "ok", "cost_usd": 0.5})
    run_async(lambda: repo.save_run(aid, done))
    runs = run_async(lambda: repo.list_runs(aid))
    assert len(runs) == 1 and runs[0].status == "ok" and runs[0].cost_usd == 0.5


def test_a_tavily_run_is_stored_and_is_a_separate_step_from_grounded(
    repo_and_id: tuple[AuditRepo, str],
) -> None:
    repo, aid = repo_and_id
    tavily = full_record(key(mode="tavily"))
    grounded = full_record(key(mode="grounded"))
    run_async(lambda: repo.save_run(aid, tavily))
    run_async(lambda: repo.save_run(aid, grounded))
    got = run_async(lambda: repo.get_run(aid, tavily.key))
    assert got is not None and got.key.search_mode == "tavily" and got.id == tavily.id
    assert len(run_async(lambda: repo.list_runs(aid))) == 2


def test_raw_answer_and_grounding_are_write_once(repo_and_id: tuple[AuditRepo, str]) -> None:
    repo, aid = repo_and_id
    record = full_record()
    run_async(lambda: repo.save_run(aid, record))
    with pytest.raises(ValueError, match="raw_answer is write-once"):
        run_async(lambda: repo.save_run(aid, record.model_copy(update={"raw_answer": "changed"})))
    with pytest.raises(ValueError, match="raw_grounding is write-once"):
        run_async(lambda: repo.save_run(aid, record.model_copy(update={"raw_grounding": {"x": 1}})))
    got = run_async(lambda: repo.get_run(aid, record.key))
    assert (
        got is not None
        and got.raw_answer == record.raw_answer
        and got.raw_grounding == record.raw_grounding
    )


def test_list_runs_keeps_insertion_order(repo_and_id: tuple[AuditRepo, str]) -> None:
    repo, aid = repo_and_id

    def save(record: RunRecord) -> None:
        run_async(lambda: repo.save_run(aid, record))

    for q in (2, 0, 1):
        save(full_record(key(q)))
    assert [r.key.question_idx for r in run_async(lambda: repo.list_runs(aid))] == [2, 0, 1]


def test_evidence_is_insert_if_absent(repo_and_id: tuple[AuditRepo, str]) -> None:
    repo, aid = repo_and_id
    first = page()
    other = page(
        evidence=SourceEvidence(
            url="https://a.example/x",
            final_url=None,
            fetch_status="unreachable",
            brand_on_page=None,
            quote=None,
            source_type="other",
            snapshot_id=None,
        )
    )
    run_async(lambda: repo.save_evidence(aid, [first]))
    run_async(
        lambda: repo.save_evidence(aid, [other, page("https://b.example/y")])
    )  # same URL again
    got = run_async(lambda: repo.load_evidence(aid))
    assert sorted(p.evidence.url for p in got) == ["https://a.example/x", "https://b.example/y"]
    a = next(p for p in got if p.evidence.url == "https://a.example/x")
    assert a.evidence.fetch_status == "ok" and a.evidence.brand_on_page is True  # first write won
    assert (a.domain, a.classification, a.brand_check_reason) == (
        "a.example",
        "model",
        "brand found",
    )


def test_state_roundtrip(repo_and_id: tuple[AuditRepo, str]) -> None:
    repo, aid = repo_and_id
    assert run_async(lambda: repo.load_state(aid)).status == "running"
    state = AuditState(
        status="capped",
        degraded=True,
        caps_hit=["cost_cap", "fetch_cap"],
        total_cost_usd=0.4321,
        search_calls=3,
        pages_fetched=9,
    )
    run_async(lambda: repo.save_state(aid, state))
    got = run_async(lambda: repo.load_state(aid))
    assert got == state


# --- the whole orchestrator on Postgres ---------------------------------------------------------------


@pytest.fixture
def pg(
    migrated_db_url: str, db: Conn
) -> Iterator[tuple[PgAuditRepo, str, async_sessionmaker[AsyncSession]]]:
    sf = sf_for(migrated_db_url)

    async def create() -> str:
        async with sf() as session, session.begin():
            audit = Audit(
                idempotency_key=f"k-{uuid.uuid4()}", request={"brand": "Acme"}, brand="Acme"
            )
            session.add(audit)
            await session.flush()
            return str(audit.id)

    yield PgAuditRepo(sf), run_async(create), sf


def run_on_pg(h: Harness, aid: str, req: Any) -> Any:
    from app.audit.runner import AuditDeps, run_audit
    from app.research.classifier import SourceClassifier

    deps = AuditDeps(
        llm=h.client,
        tracker=h.tracker,
        fetcher=h.fetcher,
        classifier=SourceClassifier(h.client, provider="gemini", model="g"),
        repo=h.repo,
        events=h.events,
        config=h.config,
        now=lambda: T0,
    )
    return run_async(lambda: run_audit(aid, req, deps))


def test_orchestrator_stores_everything_in_postgres(pg: Any) -> None:
    repo, aid, sf = pg
    h = harness(repo=repo)
    outcome = run_on_pg(h, aid, request())
    assert outcome.status == "complete"

    async def counts() -> tuple[int, int, int, Audit]:
        async with sf() as s:
            runs = (await s.execute(select(func.count()).select_from(RunRow))).scalar_one()
            cites = (await s.execute(select(func.count()).select_from(CitationRow))).scalar_one()
            ev = (await s.execute(select(func.count()).select_from(EvidenceRow))).scalar_one()
            audit = await s.get(Audit, uuid.UUID(aid))
            assert audit is not None
            return runs, cites, ev, audit

    runs, cites, ev, audit = run_async(counts)
    assert (runs, ev) == (4, 2)
    assert cites >= 2
    assert audit.status == "complete" and audit.completed_at is not None
    assert float(audit.total_cost_usd) == outcome.summary.total_cost_usd
    assert audit.pages_fetched == 2


def test_postgres_rerun_is_idempotent_and_the_summary_is_identical(pg: Any) -> None:
    repo, aid, sf = pg
    h = harness(repo=repo)
    first = run_on_pg(h, aid, request())
    h2 = harness(repo=repo)
    second = run_on_pg(h2, aid, request())
    assert h2.client.calls == [] and h2.fetcher.calls == []
    assert second.summary.model_dump() == first.summary.model_dump()

    async def n_runs() -> int:
        async with sf() as s:
            return int((await s.execute(select(func.count()).select_from(RunRow))).scalar_one())

    assert run_async(n_runs) == 4


def test_postgres_resumes_a_crashed_step_without_asking_the_model_again(pg: Any) -> None:
    from tests.test_orchestrator import Crash

    repo, aid, _ = pg
    h = harness(repo=repo)
    original = h.client._extraction

    def die_once(prompt: str) -> str:
        h.client._extraction = original  # type: ignore[method-assign]
        raise Crash

    h.client._extraction = die_once  # type: ignore[method-assign]
    with pytest.raises(Crash):
        run_on_pg(h, aid, request(questions=[QUESTIONS[0]], repeats=1))
    h2 = harness(repo=repo)
    outcome = run_on_pg(h2, aid, request(questions=[QUESTIONS[0]], repeats=1))
    assert h2.client.count("answer") == 0 and outcome.status == "complete"


def test_database_trigger_is_the_backstop_for_raw_answers(pg: Any, migrated_db_url: str) -> None:
    repo, aid, sf = pg
    run_async(lambda: repo.save_run(aid, full_record()))

    async def tamper() -> None:
        async with sf() as s, s.begin():
            row = (await s.execute(select(RunRow))).scalars().first()
            assert row is not None
            row.raw_answer = "tampered"

    with pytest.raises(Exception, match="write-once"):
        run_async(tamper)


def test_postgres_get_run_returns_the_latest_review_not_the_oldest(
    migrated_db_url: str, db: Conn
) -> None:
    from datetime import timedelta

    sf = sf_for(migrated_db_url)
    repo = PgAuditRepo(sf)

    async def go() -> None:
        async with sf() as session, session.begin():
            audit = Audit(idempotency_key="k-latest", request={"brand": "Acme"}, brand="Acme")
            session.add(audit)
            await session.flush()
            aid = str(audit.id)
        record = full_record()
        await repo.save_run(aid, record)
        async with sf() as session, session.begin():
            run_id = (await session.execute(select(RunRow.id))).scalar_one()
            for action, minutes in (("reject", 5), ("approve", 1)):  # insert newest first
                session.add(
                    ReviewRow(
                        run_id=run_id,
                        action=action,
                        reason="r",
                        reviewer="someone",
                        timestamp=T0 + timedelta(minutes=minutes),
                    )
                )
        got = await repo.get_run(aid, record.key)
        assert got is not None and got.review is not None
        assert got.review.action == "reject"  # 5 minutes after T0 beats 1 minute after T0

    run_async(go)
