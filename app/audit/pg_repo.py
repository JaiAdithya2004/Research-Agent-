"""Postgres implementation of AuditRepo (async SQLAlchemy 2.0, psycopg 3)."""

import uuid
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.records import AuditState, ReviewState, RunRecord, StepKey
from app.audit.repo import check_write_once
from app.db.models import Audit
from app.db.models import Citation as CitationRow
from app.db.models import Review as ReviewRow
from app.db.models import Run as RunRow
from app.db.models import SourceEvidence as EvidenceRow
from app.research.researcher import Classification, ResearchedPage
from app.schemas import Citation, Mention, SourceEvidence, Verdict
from app.verify.verifier import VerificationReport

_ACTION_MAP = {"accept": "approve", "approve": "approve", "correct": "correct", "reject": "reject"}
_TRUE = {"true", "yes", "mentioned"}
_FALSE = {"false", "no", "not_mentioned", "not mentioned"}


def _review_state(row: ReviewRow | None) -> ReviewState | None:
    if row is None:
        return None
    corrected: bool | None = None
    if row.corrected_value is not None:
        value = row.corrected_value.strip().lower()
        corrected = True if value in _TRUE else False if value in _FALSE else None
    return ReviewState(
        action=_ACTION_MAP[row.action],  # type: ignore[arg-type]
        corrected_mentioned=corrected,
    )


def _uuid_or_none(value: str | None) -> uuid.UUID | None:
    """Snapshot ids are UUIDs when they come from the Postgres snapshot store; anything else
    (a different store, a test double) is stored as no snapshot rather than crashing the audit."""
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _citation_from_row(row: CitationRow) -> Citation:
    return Citation(
        url=row.url,
        domain=row.domain,
        title=row.title,
        source=row.source,  # type: ignore[arg-type]
        retrieved_at=row.retrieved_at,
    )


def _to_record(row: RunRow, citations: list[CitationRow], review: ReviewRow | None) -> RunRecord:
    return RunRecord(
        id=str(row.id),
        key=StepKey(
            question_idx=row.question_idx,
            provider=row.provider,
            model=row.model,
            repeat_idx=row.repeat_idx,
            search_mode=row.search_mode,  # type: ignore[arg-type]
        ),
        question=row.question,
        status=row.status,  # type: ignore[arg-type]
        error=row.error,
        raw_answer=row.raw_answer,
        raw_grounding=row.raw_grounding,
        citations=[_citation_from_row(c) for c in citations],
        mention=Mention.model_validate(row.mention) if row.mention else None,
        verdict=Verdict(status=row.verdict_status, reason=row.verdict_reason or "n/a")  # type: ignore[arg-type]
        if row.verdict_status
        else None,
        verification=VerificationReport.model_validate(row.verification)
        if row.verification
        else None,
        extractor_version=row.extractor_version,
        latency_ms=row.latency_ms,
        cost_usd=float(row.cost_usd),
        degraded=row.degraded,
        search_calls=row.search_calls,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        review=_review_state(review),
    )


class PgAuditRepo:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    # ------------------------------------------------------------------ runs

    @staticmethod
    def _run_query(audit_id: str, key: StepKey) -> Any:
        return select(RunRow).where(
            RunRow.audit_id == uuid.UUID(audit_id),
            RunRow.question_idx == key.question_idx,
            RunRow.provider == key.provider,
            RunRow.model == key.model,
            RunRow.repeat_idx == key.repeat_idx,
            RunRow.search_mode == key.search_mode,
        )

    @staticmethod
    async def _load_one(session: AsyncSession, row: RunRow) -> RunRecord:
        cites = list(
            (
                await session.execute(
                    select(CitationRow)
                    .where(CitationRow.run_id == row.id)
                    .order_by(CitationRow.position)
                )
            ).scalars()
        )
        review = (
            await session.execute(
                select(ReviewRow)
                .where(ReviewRow.run_id == row.id)
                .order_by(ReviewRow.timestamp.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return _to_record(row, cites, review)

    async def get_run(self, audit_id: str, key: StepKey) -> RunRecord | None:
        async with self._sf() as session:
            row = (await session.execute(self._run_query(audit_id, key))).scalar_one_or_none()
            return None if row is None else await self._load_one(session, row)

    async def save_run(self, audit_id: str, record: RunRecord) -> None:
        async with self._sf() as session, session.begin():
            row = (
                await session.execute(self._run_query(audit_id, record.key).with_for_update())
            ).scalar_one_or_none()
            if row is not None:
                check_write_once(await self._load_one(session, row), record)
            else:
                row = RunRow(
                    id=uuid.UUID(record.id),
                    audit_id=uuid.UUID(audit_id),
                    question_idx=record.key.question_idx,
                    question=record.question,
                    provider=record.key.provider,
                    model=record.key.model,
                    repeat_idx=record.key.repeat_idx,
                    search_mode=record.key.search_mode,
                )
                session.add(row)
            row.status = record.status
            row.error = record.error
            row.raw_answer = record.raw_answer
            row.raw_grounding = record.raw_grounding
            row.extractor_version = record.extractor_version
            row.mention = record.mention.model_dump(mode="json") if record.mention else None
            row.verdict_status = record.verdict.status if record.verdict else None
            row.verdict_reason = record.verdict.reason if record.verdict else None
            row.verification = (
                record.verification.model_dump(mode="json") if record.verification else None
            )
            row.latency_ms = record.latency_ms
            row.cost_usd = record.cost_usd
            row.degraded = record.degraded
            row.search_calls = record.search_calls
            row.input_tokens = record.input_tokens
            row.output_tokens = record.output_tokens
            await session.flush()
            await session.execute(delete(CitationRow).where(CitationRow.run_id == row.id))
            for position, c in enumerate(record.citations):
                session.add(
                    CitationRow(
                        run_id=row.id,
                        position=position,
                        url=c.url,
                        domain=c.domain,
                        title=c.title,
                        source=c.source,
                        retrieved_at=c.retrieved_at,
                    )
                )

    async def list_runs(self, audit_id: str) -> list[RunRecord]:
        aid = uuid.UUID(audit_id)
        async with self._sf() as session:
            rows = list(
                (
                    await session.execute(
                        select(RunRow)
                        .where(RunRow.audit_id == aid)
                        .order_by(RunRow.created_at, RunRow.id)
                    )
                ).scalars()
            )
            ids = [r.id for r in rows]
            cites: dict[uuid.UUID, list[CitationRow]] = {i: [] for i in ids}
            reviews: dict[uuid.UUID, ReviewRow] = {}
            if ids:
                for c in (
                    await session.execute(
                        select(CitationRow)
                        .where(CitationRow.run_id.in_(ids))
                        .order_by(CitationRow.position)
                    )
                ).scalars():
                    cites[c.run_id].append(c)
                for rv in (
                    await session.execute(
                        select(ReviewRow)
                        .where(ReviewRow.run_id.in_(ids))
                        .order_by(ReviewRow.timestamp)
                    )
                ).scalars():
                    reviews[rv.run_id] = rv  # ascending, so the last one wins
            return [_to_record(r, cites[r.id], reviews.get(r.id)) for r in rows]

    # ------------------------------------------------------------------ evidence

    async def load_evidence(self, audit_id: str) -> list[ResearchedPage]:
        async with self._sf() as session:
            rows = (
                await session.execute(
                    select(EvidenceRow)
                    .where(EvidenceRow.audit_id == uuid.UUID(audit_id))
                    .order_by(EvidenceRow.id)
                )
            ).scalars()
            return [
                ResearchedPage(
                    evidence=SourceEvidence(
                        url=r.url,
                        final_url=r.final_url,
                        fetch_status=r.fetch_status,  # type: ignore[arg-type]
                        brand_on_page=r.brand_on_page,
                        quote=r.quote,
                        source_type=r.source_type,  # type: ignore[arg-type]
                        snapshot_id=str(r.snapshot_id) if r.snapshot_id else None,
                    ),
                    domain=r.domain,
                    cited_domain=r.cited_domain,
                    fetch_reason=r.fetch_reason,
                    from_cache=False,
                    classification=cast(Classification, r.classification or "fallback"),
                    classification_reason=r.classification_reason,
                    brand_check_reason=r.brand_check_reason,
                )
                for r in rows
            ]

    async def save_evidence(self, audit_id: str, pages: list[ResearchedPage]) -> None:
        if not pages:
            return
        values = [
            {
                "id": uuid.uuid4(),
                "audit_id": uuid.UUID(audit_id),
                "url": p.evidence.url,
                "final_url": p.evidence.final_url,
                "domain": p.domain,
                "fetch_status": p.evidence.fetch_status,
                "brand_on_page": p.evidence.brand_on_page,
                "quote": p.evidence.quote,
                "source_type": p.evidence.source_type,
                "snapshot_id": _uuid_or_none(p.evidence.snapshot_id),
                "cited_domain": p.cited_domain,
                "fetch_reason": p.fetch_reason,
                "classification": p.classification,
                "classification_reason": p.classification_reason,
                "brand_check_reason": p.brand_check_reason,
            }
            for p in pages
        ]
        async with self._sf() as session, session.begin():
            stmt = (
                pg_insert(EvidenceRow)
                .values(values)
                .on_conflict_do_nothing(constraint="uq_evidence_audit_url")
            )
            await session.execute(stmt)

    # ------------------------------------------------------------------ audit state

    async def load_state(self, audit_id: str) -> AuditState:
        async with self._sf() as session:
            audit = await session.get(Audit, uuid.UUID(audit_id))
            if audit is None:
                return AuditState()
            return AuditState(
                status=audit.status,  # type: ignore[arg-type]
                degraded=audit.degraded,
                caps_hit=list(audit.caps_hit or []),
                total_cost_usd=float(audit.total_cost_usd),
                search_calls=audit.search_calls,
                pages_fetched=audit.pages_fetched,
            )

    async def save_state(self, audit_id: str, state: AuditState) -> None:
        async with self._sf() as session, session.begin():
            audit = await session.get(Audit, uuid.UUID(audit_id), with_for_update=True)
            if audit is None:
                raise LookupError(f"audit {audit_id} does not exist")
            audit.status = state.status
            audit.degraded = state.degraded
            audit.caps_hit = list(state.caps_hit)
            audit.total_cost_usd = state.total_cost_usd
            audit.search_calls = state.search_calls
            audit.pages_fetched = state.pages_fetched
            audit.completed_at = None if state.status == "running" else datetime.now(UTC)
