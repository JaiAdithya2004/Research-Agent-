"""Server-Sent Events for audit progress, resumable with Last-Event-ID.

Frame format:  id: <seq> / event: <type> / data: <json>
The id is the event's per-audit sequence number, so a reconnecting client sends back the last id
it saw and receives exactly the events after it, with no gaps and no duplicates.
"""

import asyncio
import json
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.events import TERMINAL_EVENTS, Event
from app.audit.pg_events import fetch_events

POLL_SECONDS = 0.5
HEARTBEAT_SECONDS = 15.0
MAX_STREAM_SECONDS = 900.0  # then the client reconnects with Last-Event-ID
BATCH = 200


def format_event(event: Event) -> str:
    payload = json.dumps(
        {"seq": event.seq, "type": event.type, "data": event.data}, separators=(",", ":")
    )
    return f"id: {event.seq}\nevent: {event.type}\ndata: {payload}\n\n"


async def event_stream(
    session_factory: async_sessionmaker[AsyncSession],
    audit_id: uuid.UUID,
    after: int,
    *,
    poll_s: float = POLL_SECONDS,
    heartbeat_s: float = HEARTBEAT_SECONDS,
    max_s: float = MAX_STREAM_SECONDS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> AsyncGenerator[str]:
    """Yield SSE frames for events with seq > `after`, until a terminal event or `max_s`."""
    last = after
    started = clock()
    idle = 0.0
    while True:
        batch = await fetch_events(session_factory, audit_id, last, BATCH)
        for event in batch:
            assert event.seq is not None
            yield format_event(event)
            last = event.seq
            idle = 0.0
            if event.type in TERMINAL_EVENTS:
                return
        if batch:
            continue  # more may be waiting: read again immediately
        if clock() - started >= max_s:
            return
        await sleep(poll_s)
        idle += poll_s
        if idle >= heartbeat_s:
            yield ": keep-alive\n\n"  # a comment line: keeps proxies from closing the connection
            idle = 0.0
