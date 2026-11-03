"""GET /audits: cursor pagination and filters."""

import base64
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import psycopg
import pytest
from sqlalchemy import text

from app.audit.pg_repo import PgAuditRepo
from app.audit.records import RunRecord, StepKey
from app.db.models import Audit
from app.schemas import Mention, Verdict
from tests.api_support import ApiHarness, build_harness
from tests.conftest import run_async

Conn = psycopg.Connection[Any]
KEY = {"X-API-Key": "test-key"}
T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


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


async def add_audit(
    h: ApiHarness,
    *,
    brand: str = "Acme",
    status: str = "complete",
    minutes: int = 0,
    cost: float = 0.0,
) -> str:
    async with h.session_factory() as s, s.begin():
        audit = Audit(
            idempotency_key=f"k-{uuid.uuid4()}",
            request={"brand": brand, "category": "crm", "questions": ["Q?"]},
            brand=brand,
            status=status,
            total_cost_usd=cost,
        )
        s.add(audit)
        await s.flush()
        await s.execute(
            text("UPDATE audits SET created_at = :t WHERE id = :id"),
            {"t": T0 + timedelta(minutes=minutes), "id": audit.id},
        )
        return str(audit.id)


async def add_run(
    h: ApiHarness, audit_id: str, *, mentioned: bool, verified: bool = True, flagged: bool = False
) -> None:
    key = StepKey(
        question_idx=0, provider="gemini", model="g", repeat_idx=0, search_mode="grounded"
    )
    verdict = Verdict(status="flagged" if flagged else "verified", reason="r")
    await PgAuditRepo(h.session_factory).save_run(
        audit_id,
        RunRecord(
            key=key,
            question="Q?",
            status="ok",
            raw_answer="x",
            raw_grounding={},
            mention=Mention(
                brand_mentioned=mentioned,
                position=1 if mentioned else None,
                quote="Acme" if mentioned else None,
                sentiment="pos",
            ),
            verdict=verdict,
            latency_ms=10,
        ),
    )


async def walk(c: httpx.AsyncClient, **params: Any) -> list[str]:
    ids: list[str] = []
    cursor: str | None = None
    for _ in range(50):
        query = {**params, **({"cursor": cursor} if cursor else {})}
        page = (await c.get("/audits", params=query, headers=KEY)).json()
        ids += [i["id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            return ids
    raise AssertionError("pagination did not terminate")


def test_an_empty_list(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        r = await c.get("/audits", headers=KEY)
        assert r.status_code == 200 and r.json() == {"items": [], "next_cursor": None}
        assert (await c.get("/audits")).status_code == 401

    scenario(migrated_db_url, body)


def test_audits_are_listed_newest_first_with_the_columns_the_table_needs(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        old = await add_audit(h, minutes=0, cost=0.12)
        new = await add_audit(h, brand="Globex", minutes=5, status="capped")
        await add_run(h, old, mentioned=True)
        await add_run(h, new, mentioned=True, flagged=True)
        items = (await c.get("/audits", headers=KEY)).json()["items"]
        assert [i["id"] for i in items] == [new, old]
        o = items[1]
        assert o["brand"] == "Acme" and o["status"] == "complete" and o["mention_rate"] == 1.0
        assert (
            o["flagged_count"] == 0
            and o["total_cost_usd"] == 0.12
            and o["created_at"].startswith("2026-11-01T12:00")
        )
        n = items[0]
        assert (
            n["status"] == "capped" and n["flagged_count"] == 1 and n["mention_rate"] == 0.0
        )  # unverified claim
        assert set(o) >= {
            "id",
            "brand",
            "status",
            "mention_rate",
            "flagged_count",
            "total_cost_usd",
            "created_at",
            "degraded",
            "total_runs",
            "search_mode",
        }

    scenario(migrated_db_url, body)


def test_an_audit_with_no_runs_yet_has_a_null_mention_rate(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        await add_audit(h, status="running")
        item = (await c.get("/audits", headers=KEY)).json()["items"][0]
        assert (
            item["mention_rate"] is None and item["total_runs"] == 0 and item["status"] == "running"
        )

    scenario(migrated_db_url, body)


def test_pagination_returns_every_audit_exactly_once(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        made = [await add_audit(h, minutes=i) for i in range(11)]
        for limit in (1, 3, 5, 10, 11, 50):
            got = await walk(c, limit=limit)
            assert got == list(reversed(made)), limit

    scenario(migrated_db_url, body)


def test_audits_created_at_the_same_instant_are_still_paginated_stably(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        made = [await add_audit(h, minutes=0) for _ in range(9)]  # identical created_at
        got = await walk(c, limit=2)
        assert len(got) == 9 and set(got) == set(made)  # none missed, none repeated
        assert got == await walk(c, limit=4)  # and the order is the same however it is paged

    scenario(migrated_db_url, body)


def test_new_audits_arriving_between_pages_do_not_cause_repeats_or_gaps(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        made = [await add_audit(h, minutes=i) for i in range(6)]
        first = (await c.get("/audits", params={"limit": 3}, headers=KEY)).json()
        await add_audit(h, minutes=100)  # newest, arrives while the client is paging
        second = (
            await c.get("/audits", params={"limit": 3, "cursor": first["next_cursor"]}, headers=KEY)
        ).json()
        seen = [i["id"] for i in first["items"]] + [i["id"] for i in second["items"]]
        assert seen == list(reversed(made))

    scenario(migrated_db_url, body)


def test_default_limit_and_bounds(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        for i in range(25):
            await add_audit(h, minutes=i)
        assert len((await c.get("/audits", headers=KEY)).json()["items"]) == 20
        assert (
            len((await c.get("/audits", params={"limit": 100}, headers=KEY)).json()["items"]) == 25
        )
        for bad in (0, -1, 101):
            assert (await c.get("/audits", params={"limit": bad}, headers=KEY)).status_code == 422

    scenario(migrated_db_url, body)


def test_filter_by_status(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        done = await add_audit(h, status="complete", minutes=1)
        await add_audit(h, status="running", minutes=2)
        capped = await add_audit(h, status="capped", minutes=3)
        assert await walk(c, status="complete") == [done]
        assert await walk(c, status="capped") == [capped]
        assert await walk(c, status="failed") == []
        assert (await c.get("/audits", params={"status": "bogus"}, headers=KEY)).status_code == 422

    scenario(migrated_db_url, body)


def test_filter_by_brand_is_case_insensitive_contains_and_not_a_wildcard(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        hub = await add_audit(h, brand="HubSpot", minutes=1)
        acme = await add_audit(h, brand="Acme Corp", minutes=2)
        pct = await add_audit(h, brand="100% Pure", minutes=3)
        under = await add_audit(h, brand="a_b", minutes=4)
        assert await walk(c, brand="hubspot") == [hub]
        assert await walk(c, brand="ACME") == [acme]
        assert await walk(c, brand="ub") == [hub]  # contains
        assert await walk(c, brand="%") == [pct]  # a literal percent sign, not "match everything"
        assert await walk(c, brand="_") == [under]  # a literal underscore, not "any one character"
        assert await walk(c, brand="nothing-like-this") == []

    scenario(migrated_db_url, body)


def test_filters_combine_with_pagination(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        want = [await add_audit(h, brand="Acme", status="complete", minutes=i) for i in range(5)]
        await add_audit(h, brand="Acme", status="failed", minutes=10)
        await add_audit(h, brand="Other", status="complete", minutes=11)
        assert await walk(c, brand="acme", status="complete", limit=2) == list(reversed(want))

    scenario(migrated_db_url, body)


@pytest.mark.parametrize(
    "cursor",
    [
        "garbage",
        "!!!",
        base64.urlsafe_b64encode(b"nope").decode(),
        base64.urlsafe_b64encode(b"2026-01-01T00:00:00+00:00|not-a-uuid").decode(),
    ],
)
def test_a_malformed_cursor_is_422(migrated_db_url: str, db: Conn, cursor: str) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        assert (await c.get("/audits", params={"cursor": cursor}, headers=KEY)).status_code == 422

    scenario(migrated_db_url, body)


def test_list_items_never_expose_the_idempotency_key(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        await add_audit(h)
        assert "idempotency" not in (await c.get("/audits", headers=KEY)).text.lower()

    scenario(migrated_db_url, body)
