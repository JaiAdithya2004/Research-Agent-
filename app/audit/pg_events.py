"""Durable progress events in Postgres.

Why Postgres and not Redis: a client that reconnects with Last-Event-ID must never miss an event,
and Postgres is already the source of truth. Sequence numbers come from a counter on the audit
row, taken with UPDATE ... RETURNING inside the same transaction as the insert. The row lock makes
concurrent writers queue up, so events also become VISIBLE in sequence order: a reader that has
seen seq N can rely on every seq below N already being there.
"""

import uuid
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.events import Event, EventType
from app.db.models import AuditEvent


class PgEvents:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def emit(self, audit_id: str, type: EventType, data: dict[str, Any]) -> None:
        aid = uuid.UUID(audit_id)
        async with self._sf() as session, session.begin():
            seq = (
                await session.execute(
                    text(
                        "UPDATE audits SET event_seq = event_seq + 1, updated_at = now() "
                        "WHERE id = :id RETURNING event_seq"
                    ),
                    {"id": aid},
                )
            ).scalar_one_or_none()
            if seq is None:
                raise LookupError(f"audit {audit_id} does not exist")
            session.add(AuditEvent(audit_id=aid, seq=seq, type=type, data=data))


async def fetch_events(
    session_factory: async_sessionmaker[AsyncSession],
    audit_id: uuid.UUID,
    after: int,
    limit: int = 200,
) -> list[Event]:
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(AuditEvent)
                .where(AuditEvent.audit_id == audit_id, AuditEvent.seq > after)
                .order_by(AuditEvent.seq)
                .limit(limit)
            )
        ).scalars()
        return [
            Event(audit_id=str(r.audit_id), type=r.type, data=r.data, seq=r.seq)  # type: ignore[arg-type]
            for r in rows
        ]
