"""Durable progress events and the SSE stream, including resuming with Last-Event-ID."""

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import psycopg
import pytest
from sqlalchemy import select

from app.api.sse import event_stream, format_event
from app.audit.events import Event
from app.audit.pg_events import PgEvents, fetch_events
from app.db.models import Audit
from tests.api_support import ApiHarness, build_harness
from tests.conftest import run_async
from tests.test_orchestrator import QUESTIONS

Conn = psycopg.Connection[Any]
KEY = {"X-API-Key": "test-key"}
BODY: dict[str, Any] = {
    "brand": "Acme",
    "category": "crm",
    "competitors": ["Rival"],
    "questions": QUESTIONS,
    "repeats": 1,
    "search_mode": "grounded",
}


def scenario(url: str, body: Callable[[ApiHarness, httpx.AsyncClient], Awaitable[None]]) -> None:
    async def go() -> None:
        h = build_harness(url)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=h.app), base_url="http://t"
        ) as c:
            try:
                await body(h, c)
            finally:
                await h.close()

    run_async(go)


async def new_audit(h: ApiHarness, key: str = "k") -> uuid.UUID:
    async with h.session_factory() as s, s.begin():
        audit = Audit(idempotency_key=f"{key}-{uuid.uuid4()}", request=BODY, brand="Acme")
        s.add(audit)
        await s.flush()
        return audit.id


async def collect(gen: Any, limit: int | None = None) -> list[str]:
    frames: list[str] = []
    async for frame in gen:
        frames.append(frame)
        if limit is not None and len(frames) >= limit:
            break
    return frames


def ids(frames: list[str]) -> list[int]:
    return [int(line[4:]) for f in frames for line in f.splitlines() if line.startswith("id: ")]


async def no_wait(_: float) -> None:
    await asyncio.sleep(0)


# --- the event store ----------------------------------------------------------------------------------


def test_sequence_numbers_are_per_audit_and_gapless(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        ev = PgEvents(h.session_factory)
        a, b = await new_audit(h), await new_audit(h)
        for _ in range(3):
            await ev.emit(str(a), "run_started", {})
        await ev.emit(str(b), "run_started", {})
        await ev.emit(str(a), "audit_completed", {"status": "complete"})
        assert [e.seq for e in await fetch_events(h.session_factory, a, 0)] == [1, 2, 3, 4]
        assert [e.seq for e in await fetch_events(h.session_factory, b, 0)] == [1]

    scenario(migrated_db_url, body)


def test_concurrent_writers_get_unique_ordered_sequence_numbers(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        ev = PgEvents(h.session_factory)
        a = await new_audit(h)
        await asyncio.gather(*[ev.emit(str(a), "run_started", {"n": i}) for i in range(25)])
        got = await fetch_events(h.session_factory, a, 0, limit=100)
        assert [e.seq for e in got] == list(range(1, 26))  # unique, gapless, in order

    scenario(migrated_db_url, body)


def test_after_returns_only_later_events_in_order(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        ev = PgEvents(h.session_factory)
        a = await new_audit(h)
        for i in range(5):
            await ev.emit(str(a), "run_started", {"i": i})
        assert [e.data["i"] for e in await fetch_events(h.session_factory, a, 2)] == [2, 3, 4]
        assert await fetch_events(h.session_factory, a, 5) == []
        assert [e.seq for e in await fetch_events(h.session_factory, a, 0, limit=2)] == [1, 2]

    scenario(migrated_db_url, body)


def test_emitting_for_a_missing_audit_fails_loudly(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        with pytest.raises(LookupError):
            await PgEvents(h.session_factory).emit(str(uuid.uuid4()), "run_started", {})

    scenario(migrated_db_url, body)


def test_every_event_bumps_the_audit_heartbeat_used_by_the_sweeper(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        a = await new_audit(h)
        async with h.session_factory() as s:
            before = (await s.get(Audit, a)).updated_at  # type: ignore[union-attr]
        await asyncio.sleep(0.05)
        await PgEvents(h.session_factory).emit(str(a), "run_started", {})
        async with h.session_factory() as s:
            after = (await s.execute(select(Audit.updated_at).where(Audit.id == a))).scalar_one()
        assert after > before

    scenario(migrated_db_url, body)


# --- frame format ------------------------------------------------------------------------------------------


def test_frame_format_is_valid_sse() -> None:
    frame = format_event(
        Event(audit_id="a", type="run_verified", data={"note": "line1\nline2"}, seq=7)
    )
    assert frame.endswith("\n\n") and frame.count("\n\n") == 1
    lines = frame.strip().split("\n")
    assert (
        lines[0] == "id: 7" and lines[1] == "event: run_verified" and lines[2].startswith("data: ")
    )
    assert len(lines) == 3  # the newline inside the payload is JSON-escaped, not a raw line break
    payload = json.loads(lines[2][6:])
    assert payload == {"seq": 7, "type": "run_verified", "data": {"note": "line1\nline2"}}


# --- the stream generator -------------------------------------------------------------------------------------


async def seeded(h: ApiHarness, n: int, *, terminal: bool = False) -> uuid.UUID:
    ev = PgEvents(h.session_factory)
    a = await new_audit(h)
    for i in range(n):
        await ev.emit(str(a), "run_started", {"i": i})
    if terminal:
        await ev.emit(str(a), "audit_completed", {"status": "complete"})
    return a


def test_the_stream_replays_everything_and_stops_at_the_terminal_event(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        a = await seeded(h, 3, terminal=True)
        frames = await collect(event_stream(h.session_factory, a, 0, sleep=no_wait))
        assert ids(frames) == [1, 2, 3, 4]
        assert "event: audit_completed" in frames[-1]

    scenario(migrated_db_url, body)


def test_resuming_with_last_event_id_returns_exactly_the_missing_events(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        a = await seeded(h, 5, terminal=True)
        frames = await collect(event_stream(h.session_factory, a, 3, sleep=no_wait))
        assert ids(frames) == [4, 5, 6]

    scenario(migrated_db_url, body)


def test_a_dropped_connection_loses_nothing_and_duplicates_nothing(
    migrated_db_url: str, db: Conn
) -> None:
    """The case that matters: read some events, drop, more events arrive, reconnect."""

    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        ev = PgEvents(h.session_factory)
        a = await new_audit(h)
        for i in range(3):
            await ev.emit(str(a), "run_started", {"i": i})

        first = event_stream(h.session_factory, a, 0, sleep=no_wait)
        seen = await collect(first, limit=2)  # the client has seen events 1 and 2...
        await first.aclose()  # ...then the connection drops

        for i in range(3, 7):  # events keep arriving while the client is away
            await ev.emit(str(a), "run_started", {"i": i})
        await ev.emit(str(a), "audit_completed", {"status": "complete"})

        resumed = await collect(event_stream(h.session_factory, a, ids(seen)[-1], sleep=no_wait))
        assert ids(seen) + ids(resumed) == list(range(1, 9))  # every event, once, in order

    scenario(migrated_db_url, body)


def test_repeated_drops_at_every_position_always_reassemble_the_full_log(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        a = await seeded(h, 9, terminal=True)
        for drop_after in range(0, 10):
            got: list[int] = []
            if drop_after:
                g = event_stream(h.session_factory, a, 0, sleep=no_wait)
                got = ids(await collect(g, limit=drop_after))
                await g.aclose()
            got += ids(
                await collect(
                    event_stream(h.session_factory, a, got[-1] if got else 0, sleep=no_wait)
                )
            )
            assert got == list(range(1, 11)), drop_after

    scenario(migrated_db_url, body)


def test_events_emitted_while_the_stream_is_open_are_delivered_live(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        ev = PgEvents(h.session_factory)
        a = await new_audit(h)
        received: list[str] = []

        async def reader() -> None:
            async for frame in event_stream(h.session_factory, a, 0, poll_s=0.02):
                received.append(frame)

        task = asyncio.create_task(reader())
        await asyncio.sleep(0.1)  # the stream is open and idle
        await ev.emit(str(a), "run_started", {})
        await asyncio.sleep(0.1)
        assert ids(received) == [1]
        await ev.emit(str(a), "audit_completed", {"status": "complete"})
        await asyncio.wait_for(task, timeout=5)  # the terminal event closes the stream
        assert ids(received) == [1, 2]

    scenario(migrated_db_url, body)


def test_an_idle_stream_sends_heartbeats_and_eventually_closes(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        a = await new_audit(h)
        now = [0.0]

        async def tick(seconds: float) -> None:
            now[0] += seconds

        frames = await collect(
            event_stream(
                h.session_factory,
                a,
                0,
                poll_s=1,
                heartbeat_s=3,
                max_s=10,
                sleep=tick,
                clock=lambda: now[0],
            )
        )
        assert frames and all(f == ": keep-alive\n\n" for f in frames)
        assert 2 <= len(frames) <= 4  # roughly one per 3 idle seconds, then it closes at max_s

    scenario(migrated_db_url, body)


def test_the_stream_never_runs_past_the_terminal_event(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        ev = PgEvents(h.session_factory)
        a = await seeded(h, 1, terminal=True)
        await ev.emit(
            str(a), "run_started", {"late": True}
        )  # should never happen, but must not leak
        frames = await collect(event_stream(h.session_factory, a, 0, sleep=no_wait))
        assert ids(frames) == [1, 2]

    scenario(migrated_db_url, body)


# --- the HTTP route -------------------------------------------------------------------------------------------------


def parse_frames(text: str) -> list[dict[str, Any]]:
    out = []
    for block in text.strip().split("\n\n"):
        fields = dict(
            line.split(": ", 1) for line in block.splitlines() if not line.startswith(":")
        )
        if fields:
            out.append(
                {
                    "id": int(fields["id"]),
                    "event": fields["event"],
                    "data": json.loads(fields["data"]),
                }
            )
    return out


def test_route_streams_a_finished_audit_with_the_right_headers(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        a = await seeded(h, 2, terminal=True)
        r = await c.get(f"/audits/{a}/events", headers=KEY)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        assert "no-cache" in r.headers["cache-control"] and r.headers["x-accel-buffering"] == "no"
        frames = parse_frames(r.text)
        assert [f["id"] for f in frames] == [1, 2, 3]
        assert (
            frames[-1]["event"] == "audit_completed" and frames[0]["data"]["type"] == "run_started"
        )

    scenario(migrated_db_url, body)


def test_route_honours_the_last_event_id_header_and_the_query_parameter(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        a = await seeded(h, 4, terminal=True)
        by_header = parse_frames(
            (await c.get(f"/audits/{a}/events", headers={**KEY, "Last-Event-ID": "3"})).text
        )
        by_query = parse_frames(
            (await c.get(f"/audits/{a}/events", params={"last_event_id": 3}, headers=KEY)).text
        )
        assert [f["id"] for f in by_header] == [f["id"] for f in by_query] == [4, 5]
        both = parse_frames(
            (
                await c.get(
                    f"/audits/{a}/events",
                    params={"last_event_id": 1},
                    headers={**KEY, "Last-Event-ID": "4"},
                )
            ).text
        )
        assert [f["id"] for f in both] == [5]  # the header wins, as EventSource sends it

    scenario(migrated_db_url, body)


def test_a_finished_audit_with_nothing_new_returns_204_so_clients_stop_reconnecting(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        a = await seeded(h, 2, terminal=True)
        async with h.session_factory() as s, s.begin():
            audit = await s.get(Audit, a)
            audit.status = "complete"  # type: ignore[union-attr]
        r = await c.get(f"/audits/{a}/events", headers={**KEY, "Last-Event-ID": "3"})
        assert r.status_code == 204 and r.content == b""

    scenario(migrated_db_url, body)


@pytest.mark.parametrize("bad", ["abc", "-1", "1.5", ""])
def test_a_malformed_last_event_id_is_422(migrated_db_url: str, db: Conn, bad: str) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        a = await seeded(h, 1, terminal=True)
        r = await c.get(f"/audits/{a}/events", headers={**KEY, "Last-Event-ID": bad})
        assert r.status_code == (200 if bad == "" else 422)

    scenario(migrated_db_url, body)


def test_route_404_and_401(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        assert (await c.get(f"/audits/{uuid.uuid4()}/events", headers=KEY)).status_code == 404
        assert (await c.get("/audits/nope/events", headers=KEY)).status_code == 404
        a = await seeded(h, 1, terminal=True)
        assert (await c.get(f"/audits/{a}/events")).status_code == 401

    scenario(migrated_db_url, body)


def test_a_running_audit_stream_ends_at_the_max_duration_and_can_be_resumed(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        h.app.state.sse = {"poll_s": 0.02, "heartbeat_s": 0.05, "max_s": 2.0}
        a = await seeded(h, 2)  # running: no terminal event yet
        first = await c.get(f"/audits/{a}/events", headers=KEY)
        assert first.status_code == 200
        assert [f["id"] for f in parse_frames(first.text)] == [1, 2]
        assert ": keep-alive" in first.text
        await PgEvents(h.session_factory).emit(str(a), "audit_completed", {"status": "complete"})
        rest = await c.get(f"/audits/{a}/events", headers={**KEY, "Last-Event-ID": "2"})
        assert [f["id"] for f in parse_frames(rest.text)] == [3]

    scenario(migrated_db_url, body)


def test_a_real_audit_produces_the_documented_event_sequence(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        created = await c.post("/audits", json=BODY, headers={**KEY, "Idempotency-Key": "ev"})
        await h.queue.drain()
        r = await c.get(f"/audits/{created.json()['id']}/events", headers=KEY)
        frames = parse_frames(r.text)
        types = [f["event"] for f in frames]
        assert types[0] == "audit_started" and types[-1] == "audit_completed"
        assert {"run_started", "answer_extracted", "run_verified", "source_fetched"} <= set(types)
        assert [f["id"] for f in frames] == list(range(1, len(frames) + 1))  # gapless
        assert "Top picks" not in r.text  # progress events never carry answers or page text

    scenario(migrated_db_url, body)
