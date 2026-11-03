"""Audit creation and lookup used by the routes."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.schemas import AuditOut
from app.audit.metrics import compute_summary
from app.audit.pg_repo import PgAuditRepo
from app.audit.records import AuditState
from app.auth.authenticator import Principal
from app.db.models import Audit
from app.schemas import AuditRequest


class IdempotencyConflict(Exception):
    """The Idempotency-Key was already used with a different request body."""


class BudgetExceeded(Exception):
    def __init__(self, *, budget_usd: float, committed_usd: float, resets_in_s: int) -> None:
        super().__init__("daily cost budget reached")
        self.budget_usd = budget_usd
        self.committed_usd = committed_usd
        self.resets_in_s = resets_in_s

    @property
    def message(self) -> str:
        hours, rest = divmod(self.resets_in_s, 3600)
        return (
            f"Daily cost budget of ${self.budget_usd:.2f} reached: ${self.committed_usd:.2f} is "
            "already committed today (running audits reserve their full cost cap). The budget "
            f"resets at 00:00 UTC, in about {hours}h {rest // 60}m."
        )


# An audit that could spend less than this cannot do any useful work.
MIN_USEFUL_CAP_USD = 0.01


def utc_day_start(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


async def committed_today(session: AsyncSession, api_key_id: uuid.UUID, now: datetime) -> float:
    """Money this key has spent or reserved since 00:00 UTC.

    A running audit counts at its full cost cap (it may still spend that much); a finished one at
    what it actually spent. So the budget can never be exceeded, even by audits that run in
    parallel.
    """
    committed = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(
                        case(
                            (Audit.status == "running", Audit.cost_cap_usd),
                            else_=Audit.total_cost_usd,
                        )
                    ),
                    0,
                )
            ).where(Audit.api_key_id == api_key_id, Audit.created_at >= utc_day_start(now))
        )
    ).scalar_one()
    return float(committed)


async def create_or_get_audit(
    session: AsyncSession,
    *,
    principal: Principal,
    idempotency_key: str,
    request: AuditRequest,
    default_cap_usd: float,
    now: datetime | None = None,
) -> tuple[Audit, bool]:
    """Return (audit, created). The same key and body always returns the original audit.

    Idempotency keys are scoped to the API key that sent them. Everything below runs in one
    transaction holding a per-key advisory lock, so concurrent requests from one key are
    serialised: exactly one creates the audit, and the daily budget check cannot be raced.
    """
    now = now or datetime.now(UTC)
    payload = request.model_dump(mode="json")
    api_key_id = uuid.UUID(principal.id)
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": principal.id})

    async def existing() -> Audit | None:
        found = (
            await session.execute(
                select(Audit).where(
                    Audit.api_key_id == api_key_id, Audit.idempotency_key == idempotency_key
                )
            )
        ).scalar_one_or_none()
        if found is not None and found.request != payload:
            raise IdempotencyConflict(
                "this Idempotency-Key was already used with a different request"
            )
        return found

    if (found := await existing()) is not None:
        await session.commit()  # release the lock; a repeat is free and never blocked by budget
        return found, False

    cap = default_cap_usd
    if principal.daily_budget_usd is not None:
        committed = await committed_today(session, api_key_id, now)
        remaining = principal.daily_budget_usd - committed
        if remaining < MIN_USEFUL_CAP_USD:
            await session.rollback()
            tomorrow = utc_day_start(now) + timedelta(days=1)
            raise BudgetExceeded(
                budget_usd=principal.daily_budget_usd,
                committed_usd=committed,
                resets_in_s=max(1, int((tomorrow - now).total_seconds())),
            )
        cap = min(default_cap_usd, remaining)  # this audit can never overspend the budget

    audit = Audit(
        idempotency_key=idempotency_key,
        api_key_id=api_key_id,
        request=payload,
        brand=request.brand,
        cost_cap_usd=round(cap, 4),
        status="running",
    )
    session.add(audit)
    try:
        await session.commit()
    except IntegrityError:
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
