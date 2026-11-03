from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, QueueDep, SessionDep, require_api_key
from app.api.public import public_reason
from app.api.schemas import (
    AuditOut,
    ErrorOut,
    EvalRunOut,
    GapEntryOut,
    GapReportOut,
    SourceDetailOut,
    SourceOut,
)
from app.api.services import (
    BudgetExceeded,
    IdempotencyConflict,
    audit_out,
    create_or_get_audit,
    delete_audit,
    parse_uuid,
)
from app.api.sse import event_stream
from app.audit.pg_events import fetch_events
from app.audit.pg_repo import PgAuditRepo
from app.db.models import Audit, PageSnapshot
from app.db.models import SourceEvidence as EvidenceRow
from app.evals.store import list_eval_runs
from app.research.gaps import compute_source_gap, mentions_for_gap
from app.schemas import AuditRequest, FetchStatus, SourceType
from app.worker.queue import AuditQueue

router = APIRouter(dependencies=[Depends(require_api_key)])


def _idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str:
    key = (idempotency_key or "").strip()
    if not key:
        raise HTTPException(422, "the Idempotency-Key header is required")
    if len(key) > 255:
        raise HTTPException(422, "the Idempotency-Key header must be at most 255 characters")
    return key


async def _audit_or_404(session: AsyncSession, audit_id: str) -> Audit:
    parsed = parse_uuid(audit_id)
    audit = await session.get(Audit, parsed) if parsed else None
    if audit is None:
        raise HTTPException(404, "audit not found")
    return audit


@router.post(
    "/audits",
    response_model=AuditOut,
    status_code=202,
    responses={
        200: {"model": AuditOut, "description": "Repeat of an earlier request: the original audit"},
        422: {"model": ErrorOut},
        429: {"model": ErrorOut, "description": "Rate limit or daily cost budget reached"},
        503: {"model": ErrorOut},
    },
    summary="Create an audit (idempotent)",
)
async def create_audit(
    http: Request,
    response: Response,
    body: AuditRequest,
    key: Annotated[str, Depends(_idempotency_key)],
    principal: Annotated[Principal, Depends(require_api_key)],
    session: AsyncSession = SessionDep,
    queue: AuditQueue = QueueDep,
) -> AuditOut:
    settings = http.app.state.settings
    try:
        audit, created = await create_or_get_audit(
            session,
            principal=principal,
            idempotency_key=key,
            request=body,
            default_cap_usd=settings.default_cost_cap_usd,
        )
    except IdempotencyConflict as exc:
        raise HTTPException(422, str(exc)) from exc
    except BudgetExceeded as exc:
        raise HTTPException(
            429, exc.message, headers={"Retry-After": str(exc.resets_in_s)}
        ) from exc

    if created:
        try:
            await queue.enqueue(str(audit.id))
        except Exception as exc:
            await delete_audit(session, audit)  # no orphan: the same key can be retried
            raise HTTPException(
                503, "the audit could not be queued; retry with the same Idempotency-Key"
            ) from exc
        response.status_code = 202
    else:
        response.status_code = 200
    response.headers["Location"] = f"/audits/{audit.id}"
    return await audit_out(http.app.state.session_factory, audit)


@router.get("/audits/{audit_id}", response_model=AuditOut, responses={404: {"model": ErrorOut}})
async def get_audit(http: Request, audit_id: str, session: AsyncSession = SessionDep) -> AuditOut:
    audit = await _audit_or_404(session, audit_id)
    return await audit_out(http.app.state.session_factory, audit)


def _source_out(row: EvidenceRow, snapshot: PageSnapshot | None) -> SourceOut:
    return SourceOut(
        id=str(row.id),
        url=row.url,
        final_url=row.final_url,
        domain=row.domain,
        fetch_status=row.fetch_status,  # type: ignore[arg-type]
        fetch_reason=public_reason(row.fetch_reason),
        brand_on_page=row.brand_on_page,
        quote=row.quote,
        source_type=row.source_type,  # type: ignore[arg-type]
        classification=row.classification,
        snapshot_id=str(row.snapshot_id) if row.snapshot_id else None,
        snapshot_fetched_at=snapshot.fetched_at if snapshot else None,
    )


@router.get(
    "/audits/{audit_id}/sources",
    response_model=list[SourceOut],
    responses={404: {"model": ErrorOut}},
)
async def list_sources(
    audit_id: str,
    source_type: Annotated[SourceType | None, Query()] = None,
    fetch_status: Annotated[FetchStatus | None, Query()] = None,
    domain: Annotated[str | None, Query(max_length=255)] = None,
    session: AsyncSession = SessionDep,
) -> list[SourceOut]:
    audit = await _audit_or_404(session, audit_id)
    stmt = (
        select(EvidenceRow, PageSnapshot)
        .join(PageSnapshot, PageSnapshot.id == EvidenceRow.snapshot_id, isouter=True)
        .where(EvidenceRow.audit_id == audit.id)
        .order_by(EvidenceRow.domain, EvidenceRow.url)
    )
    if source_type:
        stmt = stmt.where(EvidenceRow.source_type == source_type)
    if fetch_status:
        stmt = stmt.where(EvidenceRow.fetch_status == fetch_status)
    if domain:
        stmt = stmt.where(EvidenceRow.domain == domain.lower().removeprefix("www."))
    return [_source_out(row, snap) for row, snap in (await session.execute(stmt)).all()]


@router.get(
    "/audits/{audit_id}/sources/{source_id}",
    response_model=SourceDetailOut,
    responses={404: {"model": ErrorOut}},
)
async def get_source(
    audit_id: str, source_id: str, session: AsyncSession = SessionDep
) -> SourceDetailOut:
    audit = await _audit_or_404(session, audit_id)
    sid = parse_uuid(source_id)
    row = await session.get(EvidenceRow, sid) if sid else None
    if row is None or row.audit_id != audit.id:
        raise HTTPException(404, "source not found")
    snapshot = await session.get(PageSnapshot, row.snapshot_id) if row.snapshot_id else None
    base = _source_out(row, snapshot)
    readable = snapshot is not None and snapshot.status == "ok"
    return SourceDetailOut(
        **base.model_dump(),
        snapshot_text=snapshot.text if readable and snapshot else None,
        content_hash=snapshot.content_hash if readable and snapshot else None,
    )


@router.get(
    "/audits/{audit_id}/gaps",
    response_model=GapReportOut,
    responses={404: {"model": ErrorOut}},
)
async def get_gaps(
    http: Request, audit_id: str, session: AsyncSession = SessionDep
) -> GapReportOut:
    audit = await _audit_or_404(session, audit_id)
    repo = PgAuditRepo(http.app.state.session_factory)
    report = compute_source_gap(mentions_for_gap(await repo.list_runs(str(audit.id))))
    rows = (
        await session.execute(select(EvidenceRow).where(EvidenceRow.audit_id == audit.id))
    ).scalars()
    by_domain: dict[str, list[str]] = {}
    for row in rows:
        for d in {row.domain, row.cited_domain}:
            by_domain.setdefault(d.lower().removeprefix("www."), []).append(str(row.id))
    return GapReportOut(
        audit_id=str(audit.id),
        entries=[
            GapEntryOut(
                domain=e.domain,
                frequency=e.frequency,
                competitors=e.competitors,
                source_ids=sorted(set(by_domain.get(e.domain, []))),
            )
            for e in report.entries
        ],
    )


@router.get(
    "/evals/runs", response_model=list[EvalRunOut], summary="Stored eval runs, newest first"
)
async def get_eval_runs(
    http: Request, limit: Annotated[int, Query(ge=1, le=500)] = 100
) -> list[EvalRunOut]:
    runs = await list_eval_runs(http.app.state.session_factory, limit)
    return [EvalRunOut(id=run_id, **result.model_dump()) for run_id, result in runs]


def _resume_point(header: str | None, query: int | None) -> int:
    """Last-Event-ID from the header (what EventSource sends), else the query parameter."""
    if header is not None and header.strip():
        value = header.strip()
        if not value.isdigit():
            raise HTTPException(422, "Last-Event-ID must be a non-negative integer")
        return int(value)
    return query or 0


@router.get(
    "/audits/{audit_id}/events",
    summary="Live progress as Server-Sent Events (resumable with Last-Event-ID)",
    responses={
        200: {"content": {"text/event-stream": {}}, "description": "Event stream"},
        204: {"description": "The audit is finished and there is nothing newer than Last-Event-ID"},
        404: {"model": ErrorOut},
        422: {"model": ErrorOut},
    },
)
async def audit_events(
    http: Request,
    audit_id: str,
    last_event_id: Annotated[int | None, Query(ge=0)] = None,
    last_event_id_header: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> Response:
    after = _resume_point(last_event_id_header, last_event_id)
    factory = http.app.state.session_factory
    # Short-lived sessions only: a long-lived stream must not hold a database connection.
    parsed = parse_uuid(audit_id)
    async with factory() as session:
        audit = await session.get(Audit, parsed) if parsed else None
        status = audit.status if audit is not None else None
    if audit is None or parsed is None:
        raise HTTPException(404, "audit not found")
    if status != "running" and not await fetch_events(factory, parsed, after, 1):
        # EventSource reconnects forever on a closed stream; 204 tells it to stop.
        return Response(status_code=204)
    options = getattr(http.app.state, "sse", {})
    return StreamingResponse(
        event_stream(factory, parsed, after, **options),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
