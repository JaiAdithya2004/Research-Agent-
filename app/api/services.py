"""Audit creation and lookup used by the routes."""

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.schemas import AuditOut
from app.audit.metrics import compute_summary
from app.audit.pg_repo import PgAuditRepo
from app.audit.records import AuditState
from app.db.models import Audit
from app.schemas import AuditRequest


class IdempotencyConflict(Exception):
    """The Idempotency-Key was already used with a different request body."""


async def create_or_get_audit(
    session: AsyncSession, *, idempotency_key: str, request: AuditRequest, cost_cap_usd: float
) -> tuple[Audit, bool]:
    """Return (audit, created). The same key and body always returns the original audit."""
    payload = request.model_dump(mode="json")

    async def existing() -> Audit | None:
        found = (
            await session.execute(select(Audit).where(Audit.idempotency_key == idempotency_key))
        ).scalar_one_or_none()
        if found is not None and found.request != payload:
            raise IdempotencyConflict(
                "this Idempotency-Key was already used with a different request"
            )
        return found

    if (found := await existing()) is not None:
        return found, False
    audit = Audit(
        idempotency_key=idempotency_key,
        request=payload,
        brand=request.brand,
        cost_cap_usd=cost_cap_usd,
        status="running",
    )
    session.add(audit)
    try:
        await session.commit()
    except IntegrityError:
        # A concurrent request with the same key won the race: return its audit.
        await session.rollback()
        found = await existing()
        assert found is not None
        return found, False
    await session.refresh(audit)
    return audit, True


async def delete_audit(session: AsyncSession, audit: Audit) -> None:
    """Compensate for a failed enqueue so the client can retry with the same key."""
    await session.delete(audit)
    await session.commit()


def parse_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


async def audit_out(session_factory: async_sessionmaker[AsyncSession], audit: Audit) -> AuditOut:
    repo = PgAuditRepo(session_factory)
    audit_id = str(audit.id)
    runs = await repo.list_runs(audit_id)
    state = AuditState(
        status=audit.status,  # type: ignore[arg-type]
        degraded=audit.degraded,
        caps_hit=list(audit.caps_hit or []),
        total_cost_usd=float(audit.total_cost_usd),
        search_calls=audit.search_calls,
        pages_fetched=audit.pages_fetched,
    )
    request = AuditRequest.model_validate(audit.request)
    return AuditOut(
        id=audit_id,
        status=state.status,
        brand=audit.brand,
        created_at=audit.created_at,
        completed_at=audit.completed_at,
        degraded=state.degraded or any(r.degraded for r in runs),
        caps_hit=state.caps_hit,
        request=request,
        summary=compute_summary(runs, search_mode=request.search_mode, state=state)
        if runs
        else None,
    )
