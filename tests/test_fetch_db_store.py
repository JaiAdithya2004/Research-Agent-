"""DbSnapshotStore against the real (test) Postgres; the network is still mocked."""

from datetime import timedelta
from typing import Any

import psycopg
import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.db.models import PageSnapshot
from app.db.session import make_engine, make_session_factory
from app.fetch.db_store import DbSnapshotStore
from tests.conftest import run_async
from tests.test_safe_fetch import HTML_OK, T0, Net, Page, html, make_fetcher

Conn = psycopg.Connection[Any]


def with_store(url: str, body: Any) -> Any:
    async def go() -> Any:
        engine = make_engine(url)
        try:
            return await body(DbSnapshotStore(make_session_factory(engine)), engine)
        finally:
            await engine.dispose()

    return run_async(go)


def test_add_and_latest_roundtrip(migrated_db_url: str, db: Conn) -> None:
    async def body(store: DbSnapshotStore, engine: Any) -> Any:
        old = await store.add(
            url="http://e.com/p",
            final_url="http://e.com/p",
            fetched_at=T0,
            status="ok",
            text="old text",
            content_hash="h1",
        )
        new = await store.add(
            url="http://e.com/p",
            final_url="http://e.com/p",
            fetched_at=T0 + timedelta(hours=1),
            status="ok",
            text="new text",
            content_hash="h2",
        )
        got = await store.latest_cacheable("http://e.com/p", T0 - timedelta(days=1))
        return old, new, got

    old, new, got = with_store(migrated_db_url, body)
    assert old != new
    assert got is not None and got.id == new and got.text == "new text"
    assert got.content_hash == "h2" and got.fetched_at == T0 + timedelta(hours=1)


def test_latest_ignores_old_other_url_and_non_cacheable(migrated_db_url: str, db: Conn) -> None:
    async def body(store: DbSnapshotStore, engine: Any) -> list[Any]:
        await store.add(
            url="http://e.com/a",
            final_url=None,
            fetched_at=T0,
            status="ok",
            text="a",
            content_hash="h",
        )
        await store.add(
            url="http://e.com/b",
            final_url=None,
            fetched_at=T0,
            status="unreachable",
            text=None,
            content_hash=None,
        )
        await store.add(
            url="http://e.com/c",
            final_url=None,
            fetched_at=T0,
            status="blocked",
            text=None,
            content_hash=None,
        )
        since = T0 - timedelta(hours=1)
        return [
            await store.latest_cacheable("http://e.com/a", T0 + timedelta(minutes=1)),  # too old
            await store.latest_cacheable("http://e.com/zzz", since),  # other URL
            await store.latest_cacheable("http://e.com/b", since),  # unreachable: not cacheable
            await store.latest_cacheable("http://e.com/c", since),  # blocked: not cacheable
            await store.latest_cacheable("http://e.com/a", since),  # the only valid hit
        ]

    results = with_store(migrated_db_url, body)
    assert results[:4] == [None, None, None, None]
    assert results[4] is not None


def test_fetcher_end_to_end_with_postgres_snapshots(migrated_db_url: str, db: Conn) -> None:
    net = Net(pages={"example.com/p": html(), "example.com/gone": Page(404)})

    async def body(store: DbSnapshotStore, engine: Any) -> tuple[Any, Any, Any, list[Any]]:
        fetcher = make_fetcher(net, store=store)  # type: ignore[arg-type]
        first = await fetcher.fetch("http://example.com/p")
        second = await fetcher.fetch("http://example.com/p")
        await fetcher.fetch("http://example.com/gone")
        async with make_session_factory(engine)() as session:
            rows = list((await session.execute(select(PageSnapshot))).scalars())
        return first, second, None, rows

    first, second, _, rows = with_store(migrated_db_url, body)
    assert first.fetch_status == "ok" and first.from_cache is False
    assert second.from_cache is True and second.snapshot_id == first.snapshot_id
    assert sorted(r.status for r in rows) == ["ok", "unreachable"]  # the cache hit added no row
    ok = next(r for r in rows if r.status == "ok")
    assert ok.text == first.text and ok.content_hash == first.content_hash
    assert ok.fetched_at == T0
    assert "Acme is a great project management tool" in (ok.text or "")


def test_stored_snapshots_cannot_be_modified(migrated_db_url: str, db: Conn) -> None:
    async def body(store: DbSnapshotStore, engine: Any) -> None:
        sid = await store.add(
            url="http://e.com/p",
            final_url=None,
            fetched_at=T0,
            status="ok",
            text=HTML_OK,
            content_hash="h",
        )
        async with make_session_factory(engine)() as session:
            row = (await session.execute(select(PageSnapshot))).scalar_one()
            assert str(row.id) == sid
            row.text = "tampered"
            await session.commit()

    with pytest.raises(DBAPIError, match="append-only"):
        with_store(migrated_db_url, body)


def test_nul_byte_page_is_stored_in_postgres(migrated_db_url: str, db: Conn) -> None:
    """BUG regression: a NUL byte used to make the insert fail and the page an internal_error."""
    net = Net(
        pages={
            "example.com/nul": Page(
                200,
                b"Acme is a nice tool for teams.\x00 and more words follow here for length",
                {"content-type": "text/plain"},
            )
        }
    )

    async def body(store: DbSnapshotStore, engine: Any) -> Any:
        return await make_fetcher(net, store=store).fetch("http://example.com/nul")  # type: ignore[arg-type]

    result = with_store(migrated_db_url, body)
    assert result.fetch_status == "ok" and "\x00" not in (result.text or "")
