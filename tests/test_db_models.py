import uuid
from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest
from psycopg import errors
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Audit, Citation, PageSnapshot, Review, Run, SourceEvidence
from app.db.session import make_engine, make_session_factory
from tests.conftest import run_async

NOW = datetime(2026, 11, 3, tzinfo=UTC)
Conn = psycopg.Connection[Any]


def make_audit(key: str = "key-1") -> Audit:
    return Audit(idempotency_key=key, request={"brand": "Acme"})


def make_run(audit_id: uuid.UUID, **kw: Any) -> Run:
    data: dict[str, Any] = {
        "audit_id": audit_id,
        "question_idx": 0,
        "question": "best tool?",
        "model": "gemini-x",
        "repeat_idx": 0,
        "search_mode": "grounded",
    }
    data.update(kw)
    return Run(**data)


def with_session(migrated_db_url: str, body: Any) -> Any:
    async def go() -> Any:
        engine = make_engine(migrated_db_url)
        try:
            async with make_session_factory(engine)() as session:
                return await body(session)
        finally:
            await engine.dispose()

    return run_async(go)


def test_all_tables_exist(db: Conn) -> None:
    rows = db.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    ).fetchall()
    names = {r[0] for r in rows}
    assert {
        "audits",
        "runs",
        "citations",
        "page_snapshots",
        "source_evidence",
        "reviews",
    } <= names


def test_orm_roundtrip_keeps_raw_answer_and_grounding(migrated_db_url: str, db: Conn) -> None:
    grounding = {"chunks": [{"uri": "https://example.com", "title": "Ex"}], "n": 1}

    async def body(session: AsyncSession) -> Run:
        audit = make_audit()
        session.add(audit)
        await session.flush()
        run = make_run(audit.id, raw_answer="Acme is good.", raw_grounding=grounding, status="ok")
        session.add(run)
        session.add(
            Citation(
                run=run,
                url="https://example.com",
                domain="example.com",
                source="grounding_metadata",
                retrieved_at=NOW,
            )
        )
        await session.commit()
        got = (await session.execute(select(Run))).scalar_one()
        return got

    run = with_session(migrated_db_url, body)
    assert run.raw_answer == "Acme is good."
    assert run.raw_grounding == grounding


def test_idempotency_key_is_unique(migrated_db_url: str, db: Conn) -> None:
    async def body(session: AsyncSession) -> None:
        session.add(make_audit("same"))
        await session.commit()
        session.add(make_audit("same"))
        await session.commit()

    with pytest.raises(IntegrityError):
        with_session(migrated_db_url, body)


def test_run_step_is_unique_per_question_model_repeat_mode(migrated_db_url: str, db: Conn) -> None:
    async def body(session: AsyncSession) -> None:
        audit = make_audit()
        session.add(audit)
        await session.flush()
        session.add(make_run(audit.id))
        await session.commit()
        session.add(make_run(audit.id))
        await session.commit()

    with pytest.raises(IntegrityError):
        with_session(migrated_db_url, body)


def _insert_audit_and_run(db: Conn, **run_cols: Any) -> uuid.UUID:
    audit_id = uuid.uuid4()
    run_id = uuid.uuid4()
    db.execute(
        "INSERT INTO audits (id, idempotency_key, request, status, degraded, cost_cap_usd, "
        "total_cost_usd, search_calls, pages_fetched) "
        "VALUES (%s, 'k', '{}', 'running', false, 0.75, 0, 0, 0)",
        (audit_id,),
    )
    cols = {
        "id": run_id,
        "audit_id": audit_id,
        "question_idx": 0,
        "question": "q",
        "model": "m",
        "repeat_idx": 0,
        "search_mode": "grounded",
        "status": "pending",
        "cost_usd": 0,
        **run_cols,
    }
    names = ", ".join(cols)
    marks = ", ".join(["%s"] * len(cols))
    db.execute(f"INSERT INTO runs ({names}) VALUES ({marks})", list(cols.values()))
    return run_id


def test_raw_answer_cannot_be_overwritten(db: Conn) -> None:
    run_id = _insert_audit_and_run(db, raw_answer="original")
    with pytest.raises(errors.RaiseException, match="raw_answer is write-once"):
        db.execute("UPDATE runs SET raw_answer = 'edited' WHERE id = %s", (run_id,))
    with pytest.raises(errors.RaiseException, match="raw_answer is write-once"):
        db.execute("UPDATE runs SET raw_answer = NULL WHERE id = %s", (run_id,))
    row = db.execute("SELECT raw_answer FROM runs WHERE id = %s", (run_id,)).fetchone()
    assert row == ("original",)


def test_raw_grounding_cannot_be_overwritten(db: Conn) -> None:
    run_id = _insert_audit_and_run(db, raw_grounding='{"a": 1}')
    with pytest.raises(errors.RaiseException, match="raw_grounding is write-once"):
        db.execute("UPDATE runs SET raw_grounding = '{\"a\": 2}' WHERE id = %s", (run_id,))
    row = db.execute("SELECT raw_grounding FROM runs WHERE id = %s", (run_id,)).fetchone()
    assert row == ({"a": 1},)


def test_raw_columns_can_be_written_once_after_pending(db: Conn) -> None:
    run_id = _insert_audit_and_run(db)
    db.execute(
        "UPDATE runs SET raw_answer = 'first', raw_grounding = '{}', status = 'ok' WHERE id = %s",
        (run_id,),
    )
    # Derived columns stay updatable without touching raw data (e.g. extractor v2).
    db.execute(
        "UPDATE runs SET verdict_status = 'flagged', verdict_reason = 'x' WHERE id = %s", (run_id,)
    )
    row = db.execute("SELECT raw_answer, verdict_status FROM runs WHERE id = %s", (run_id,))
    assert row.fetchone() == ("first", "flagged")


def _insert_snapshot(db: Conn) -> uuid.UUID:
    snap_id = uuid.uuid4()
    db.execute(
        "INSERT INTO page_snapshots (id, url, fetched_at, status, text, content_hash) "
        "VALUES (%s, 'https://e.com', now(), 'ok', 'hello', 'abc')",
        (snap_id,),
    )
    return snap_id


def test_page_snapshots_are_append_only(db: Conn) -> None:
    snap_id = _insert_snapshot(db)
    with pytest.raises(errors.RaiseException, match="append-only"):
        db.execute("UPDATE page_snapshots SET text = 'changed' WHERE id = %s", (snap_id,))
    with pytest.raises(errors.RaiseException, match="append-only"):
        db.execute("DELETE FROM page_snapshots WHERE id = %s", (snap_id,))


def test_reviews_are_separate_and_append_only(db: Conn) -> None:
    run_id = _insert_audit_and_run(db, raw_answer="original")
    review_id = uuid.uuid4()
    db.execute(
        "INSERT INTO reviews (id, run_id, action, corrected_value, reason, reviewer, timestamp) "
        "VALUES (%s, %s, 'correct', 'neg', 'wrong', 'sam', now())",
        (review_id, run_id),
    )
    # The run is untouched by a review.
    assert db.execute("SELECT raw_answer FROM runs WHERE id = %s", (run_id,)).fetchone() == (
        "original",
    )
    with pytest.raises(errors.RaiseException, match="append-only"):
        db.execute("UPDATE reviews SET reason = 'x' WHERE id = %s", (review_id,))


def test_review_correct_requires_value_in_db(db: Conn) -> None:
    run_id = _insert_audit_and_run(db)
    with pytest.raises(errors.CheckViolation):
        db.execute(
            "INSERT INTO reviews (id, run_id, action, reason, reviewer, timestamp) "
            "VALUES (%s, %s, 'correct', 'r', 'sam', now())",
            (uuid.uuid4(), run_id),
        )


def test_unfetched_evidence_cannot_claim_brand_absent(db: Conn) -> None:
    run_id = _insert_audit_and_run(db)
    audit_id = db.execute("SELECT audit_id FROM runs WHERE id = %s", (run_id,)).fetchone()
    assert audit_id is not None
    insert = (
        "INSERT INTO source_evidence (id, audit_id, url, domain, fetch_status, brand_on_page, "
        "source_type) VALUES (%s, %s, %s, 'e.com', %s, %s, 'other')"
    )
    with pytest.raises(errors.CheckViolation):
        db.execute(insert, (uuid.uuid4(), audit_id[0], "https://e.com/a", "unreachable", False))
    db.execute(insert, (uuid.uuid4(), audit_id[0], "https://e.com/b", "unreachable", None))
    db.execute(insert, (uuid.uuid4(), audit_id[0], "https://e.com/c", "ok", False))
    # One evidence row per URL per audit (makes research idempotent across retried jobs).
    with pytest.raises(errors.UniqueViolation):
        db.execute(insert, (uuid.uuid4(), audit_id[0], "https://e.com/c", "ok", True))


def test_invalid_enum_values_rejected(db: Conn) -> None:
    with pytest.raises(errors.CheckViolation):
        db.execute(
            "INSERT INTO page_snapshots (id, url, fetched_at, status) "
            "VALUES (%s, 'u', now(), 'bogus')",
            (uuid.uuid4(),),
        )


def test_evidence_can_reference_snapshot_via_orm(migrated_db_url: str, db: Conn) -> None:
    async def body(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID | None]:
        audit = make_audit()
        snap = PageSnapshot(
            url="https://e.com", fetched_at=NOW, status="ok", text="Acme here", content_hash="h"
        )
        session.add_all([audit, snap])
        await session.flush()
        ev = SourceEvidence(
            audit_id=audit.id,
            url="https://e.com",
            domain="e.com",
            fetch_status="ok",
            brand_on_page=True,
            quote="Acme",
            source_type="review",
            snapshot_id=snap.id,
        )
        session.add(ev)
        await session.commit()
        return snap.id, ev.snapshot_id

    snap_id, ev_snap = with_session(migrated_db_url, body)
    assert snap_id == ev_snap


def test_review_orm_roundtrip(migrated_db_url: str, db: Conn) -> None:
    async def body(session: AsyncSession) -> str:
        audit = make_audit()
        session.add(audit)
        await session.flush()
        run = make_run(audit.id)
        session.add(run)
        await session.flush()
        session.add(
            Review(run_id=run.id, action="approve", reason="ok", reviewer="sam", timestamp=NOW)
        )
        await session.commit()
        return (await session.execute(select(Review))).scalar_one().reviewer

    assert with_session(migrated_db_url, body) == "sam"
