"""Audit list, human review, the review queue, and run detail."""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import exists, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.cursor import decode_cursor, encode_cursor
from app.api.deps import Principal, SessionDep, require_api_key
from app.api.public import public_reason
from app.api.review_schemas import (
    AuditListItem,
    AuditListPage,
    CitationOut,
    EvidenceBrief,
    FindingOut,
    QueueItem,
    QueuePage,
    ReviewDetail,
    ReviewIn,
    ReviewOut,
    RunDetailOut,
    RunSummaryOut,
    SpanOut,
)
from app.api.schemas import ErrorOut
from app.api.services import audit_out, parse_uuid
from app.audit.metrics import run_outcome
from app.audit.pg_repo import PgAuditRepo
from app.db.models import Audit
from app.db.models import Review as ReviewRow
from app.db.models import Run as RunRow
from app.db.models import SourceEvidence as EvidenceRow
from app.schemas import AuditStatus, Mention
from app.verify.verifier import locate_quote, url_key

router = APIRouter(dependencies=[Depends(require_api_key)])


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _highlights(raw: str | None, mention: Mention | None) -> list[SpanOut]:
    if not raw or mention is None or not mention.quote:
        return []
    span = locate_quote(raw, mention.quote)
    return [SpanOut(start=span[0], end=span[1])] if span else []


# --- audit list ---


async def _audit_is_deleted(session: AsyncSession, audit_id: uuid.UUID) -> bool:
    audit = await session.get(Audit, audit_id)
    return audit is None or audit.deleted_at is not None


@router.get("/audits", response_model=AuditListPage, responses={422: {"model": ErrorOut}})
async def list_audits(
    http: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query()] = None,
    status: Annotated[AuditStatus | None, Query()] = None,
    brand: Annotated[str | None, Query(max_length=255)] = None,
    session: AsyncSession = SessionDep,
) -> AuditListPage:
    stmt = (
        select(Audit)
        .where(Audit.deleted_at.is_(None))
        .order_by(Audit.created_at.desc(), Audit.id.desc())
        .limit(limit + 1)
    )
    if status:
        stmt = stmt.where(Audit.status == status)
    if brand and brand.strip():
        stmt = stmt.where(Audit.brand.ilike(f"%{_like_escape(brand.strip())}%", escape="\\"))
    if cursor:
        stamp, row_id = decode_cursor(cursor)
        stmt = stmt.where(tuple_(Audit.created_at, Audit.id) < tuple_(stamp, row_id))
    rows = list((await session.execute(stmt)).scalars())
    page, more = rows[:limit], len(rows) > limit

    items: list[AuditListItem] = []
    for audit in page:
        out = await audit_out(http.app.state.session_factory, audit)
        summary = out.summary
        items.append(
            AuditListItem(
                id=out.id,
                brand=out.brand,
                status=out.status,
                created_at=out.created_at,
                completed_at=out.completed_at,
                search_mode=out.request.search_mode,
                mention_rate=summary.mention_rate if summary else None,
                flagged_count=summary.flagged_count if summary else 0,
                total_cost_usd=float(audit.total_cost_usd),
                degraded=out.degraded,
                total_runs=summary.total_runs if summary else 0,
                failed_runs=summary.failed_runs if summary else 0,
                caps_hit=out.caps_hit,
            )
        )
    last = page[-1] if page else None
    return AuditListPage(
        items=items,
        next_cursor=encode_cursor(last.created_at, last.id) if more and last else None,
    )


# --- review ---


@router.post(
    "/mentions/{run_id}/review",
    response_model=ReviewOut,
    status_code=201,
    summary="Approve, correct or reject an extracted mention",
    responses={404: {"model": ErrorOut}, 409: {"model": ErrorOut}, 422: {"model": ErrorOut}},
)
async def review_mention(
    http: Request,
    run_id: str,
    body: ReviewIn,
    principal: Annotated[Principal, Depends(require_api_key)],
    session: AsyncSession = SessionDep,
) -> ReviewOut:
    rid = parse_uuid(run_id)
    run = await session.get(RunRow, rid) if rid else None
    if run is None or await _audit_is_deleted(session, run.audit_id):
        raise HTTPException(404, "run not found")
    if run.status != "ok" or not run.mention:
        raise HTTPException(409, "this run has no extracted mention to review")

    # Reviews are appended to their own table. The run's raw answer, grounding metadata,
    # extracted mention and verdict are never touched.
    review = ReviewRow(
        run_id=run.id,
        action=body.action,
        corrected_value=body.corrected_value.strip() if body.corrected_value else None,
        reason=body.reason,
        reviewer=principal.name,
        timestamp=datetime.now(UTC),
    )
    session.add(review)
    await session.commit()
    await session.refresh(review)

    audit = await session.get(Audit, run.audit_id)
    assert audit is not None
    summary = (await audit_out(http.app.state.session_factory, audit)).summary
    return ReviewOut(
        id=str(review.id),
        run_id=str(run.id),
        audit_id=str(run.audit_id),
        action=body.action,
        corrected_value=review.corrected_value,
        reason=review.reason,
        reviewer=review.reviewer,
        timestamp=review.timestamp,
        summary=summary,
    )


@router.get(
    "/review/queue",
    response_model=QueuePage,
    summary="Flagged mentions that still need a human decision",
    responses={422: {"model": ErrorOut}},
)
async def review_queue(
    audit_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query()] = None,
    session: AsyncSession = SessionDep,
) -> QueuePage:
    stmt = (
        select(RunRow, Audit.brand)
        .join(Audit, Audit.id == RunRow.audit_id)
        .where(
            Audit.deleted_at.is_(None),
            RunRow.status == "ok",
            RunRow.verdict_status == "flagged",
            ~exists().where(ReviewRow.run_id == RunRow.id),
        )
        .order_by(RunRow.created_at.desc(), RunRow.id.desc())
        .limit(limit + 1)
    )
    if audit_id:
        parsed = parse_uuid(audit_id)
        if parsed is None:
            raise HTTPException(422, "audit_id must be a UUID")
        stmt = stmt.where(RunRow.audit_id == parsed)
    if cursor:
        stamp, row_id = decode_cursor(cursor)
        stmt = stmt.where(tuple_(RunRow.created_at, RunRow.id) < tuple_(stamp, row_id))
    rows = (await session.execute(stmt)).all()
    page, more = rows[:limit], len(rows) > limit

    items: list[QueueItem] = []
    for run, brand in page:
        mention = Mention.model_validate(run.mention) if run.mention else None
        if mention is None:
            continue
        items.append(
            QueueItem(
                run_id=str(run.id),
                audit_id=str(run.audit_id),
                brand=brand,
                question=run.question,
                provider=run.provider,
                model=run.model,
                search_mode=run.search_mode,  # type: ignore[arg-type]
                repeat_idx=run.repeat_idx,
                verdict_reason=run.verdict_reason or "",
                quote=mention.quote,
                brand_mentioned=mention.brand_mentioned,
                position=mention.position,
                raw_answer=run.raw_answer,
                highlights=_highlights(run.raw_answer, mention),
                created_at=run.created_at,
            )
        )
    last = page[-1][0] if page else None
    return QueuePage(
        items=items, next_cursor=encode_cursor(last.created_at, last.id) if more and last else None
    )


# --------------------------------------------------------------------------------- runs and detail


@router.get(
    "/audits/{audit_id}/runs",
    response_model=list[RunSummaryOut],
    responses={404: {"model": ErrorOut}},
)
async def list_runs(
    http: Request, audit_id: str, session: AsyncSession = SessionDep
) -> list[RunSummaryOut]:
    parsed = parse_uuid(audit_id)
    parent = await session.get(Audit, parsed) if parsed else None
    if parent is None or parent.deleted_at is not None:
        raise HTTPException(404, "audit not found")
    runs = await PgAuditRepo(http.app.state.session_factory).list_runs(str(parsed))
    ordered = sorted(
        runs,
        key=lambda r: (
            r.key.question_idx,
            r.key.repeat_idx,
            r.key.provider,
            r.key.model,
            r.key.search_mode,
        ),
    )
    return [
        RunSummaryOut(
            id=r.id,
            question_idx=r.key.question_idx,
            question=r.question,
            provider=r.key.provider,
            model=r.key.model,
            repeat_idx=r.key.repeat_idx,
            search_mode=r.key.search_mode,
            status=r.status,
            error=r.error,
            brand_mentioned=run_outcome(r),
            verdict_status=r.verdict.status if r.verdict else None,
            reviewed=r.review is not None,
            degraded=r.degraded,
            latency_ms=r.latency_ms,
            cost_usd=r.cost_usd,
        )
        for r in ordered
    ]


@router.get("/runs/{run_id}", response_model=RunDetailOut, responses={404: {"model": ErrorOut}})
async def get_run(http: Request, run_id: str, session: AsyncSession = SessionDep) -> RunDetailOut:
    rid = parse_uuid(run_id)
    row = await session.get(RunRow, rid) if rid else None
    if row is None or await _audit_is_deleted(session, row.audit_id):
        raise HTTPException(404, "run not found")
    repo = PgAuditRepo(http.app.state.session_factory)
    run = next((r for r in await repo.list_runs(str(row.audit_id)) if r.id == str(row.id)), None)
    if run is None:  # pragma: no cover - the row was just read
        raise HTTPException(404, "run not found")

    evidence = {
        url_key(e.url): e
        for e in (
            await session.execute(select(EvidenceRow).where(EvidenceRow.audit_id == row.audit_id))
        ).scalars()
    }
    citations: list[CitationOut] = []
    for c in run.citations:
        ev = evidence.get(url_key(c.url))
        citations.append(
            CitationOut(
                url=c.url,
                domain=c.domain,
                title=c.title,
                source=c.source,
                unverified=c.unverified,
                evidence=EvidenceBrief(
                    source_id=str(ev.id),
                    domain=ev.domain,
                    fetch_status=ev.fetch_status,  # type: ignore[arg-type]
                    fetch_reason=public_reason(ev.fetch_reason),
                    brand_on_page=ev.brand_on_page,
                    source_type=ev.source_type,  # type: ignore[arg-type]
                )
                if ev
                else None,
            )
        )

    review_row = (
        await session.execute(
            select(ReviewRow)
            .where(ReviewRow.run_id == row.id)
            .order_by(ReviewRow.timestamp.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    findings = run.verification.findings if run.verification else []
    return RunDetailOut(
        id=run.id,
        audit_id=str(row.audit_id),
        question=run.question,
        provider=run.key.provider,
        model=run.key.model,
        repeat_idx=run.key.repeat_idx,
        search_mode=run.key.search_mode,
        status=run.status,
        error=run.error,
        raw_answer=run.raw_answer,
        mention=run.mention,
        verdict=run.verdict,
        findings=[FindingOut(code=str(f.code), status=f.status, reason=f.reason) for f in findings],
        highlights=_highlights(run.raw_answer, run.mention),
        citations=citations,
        review=ReviewDetail(
            action=review_row.action,  # type: ignore[arg-type]
            corrected_value=review_row.corrected_value,
            reason=review_row.reason,
            reviewer=review_row.reviewer,
            timestamp=review_row.timestamp,
        )
        if review_row
        else None,
        brand_mentioned=run_outcome(run),
        degraded=run.degraded,
        latency_ms=run.latency_ms,
        cost_usd=run.cost_usd,
    )
