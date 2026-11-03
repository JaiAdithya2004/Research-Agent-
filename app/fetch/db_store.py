"""Postgres-backed snapshot store: rows go into page_snapshots, which is append-only."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import PageSnapshot
from app.fetch.safe_fetch import CACHEABLE_STATUSES, SnapshotRecord
from app.schemas import FetchStatus


class DbSnapshotStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def latest_cacheable(self, url: str, since: datetime) -> SnapshotRecord | None:
        stmt = (
            select(PageSnapshot)
            .where(
                PageSnapshot.url == url,
                PageSnapshot.status.in_(CACHEABLE_STATUSES),
                PageSnapshot.fetched_at >= since,
            )
            .order_by(PageSnapshot.fetched_at.desc())
            .limit(1)
        )
        async with self._session_factory() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return SnapshotRecord(
            id=str(row.id),
            url=row.url,
            final_url=row.final_url,
            fetched_at=row.fetched_at,
            status=row.status,  # type: ignore[arg-type]
            text=row.text,
            content_hash=row.content_hash,
        )

    async def add(
        self,
        *,
        url: str,
        final_url: str | None,
        fetched_at: datetime,
        status: FetchStatus,
        text: str | None,
        content_hash: str | None,
    ) -> str:
        snapshot = PageSnapshot(
            url=url,
            final_url=final_url,
            fetched_at=fetched_at,
            status=status,
            text=text,
            content_hash=content_hash,
        )
        async with self._session_factory() as session:
            session.add(snapshot)
            await session.commit()
            return str(snapshot.id)
