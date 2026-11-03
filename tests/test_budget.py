"""Per-key daily cost budget: 429 with a clear message, never overspent, safe under concurrency."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import psycopg
from sqlalchemy import select, text

from app.api.services import MIN_USEFUL_CAP_USD, utc_day_start
from app.auth.authenticator import Principal
from app.config import Settings
from app.db.models import Audit
from tests.api_support import TEST_KEY_ID, ApiHarness, build_harness
from tests.conftest import run_async
from tests.test_orchestrator import QUESTIONS

Conn = psycopg.Connection[Any]
HDR = {"X-API-Key": "test-key"}
BODY: dict[str, Any] = {
    "brand": "Acme",
    "category": "crm",
    "competitors": ["Rival"],
    "questions": QUESTIONS,
    "repeats": 1,
}


def budgeted(amount: float | None) -> Principal:
    return Principal(id=TEST_KEY_ID, name="budgeted", daily_budget_usd=amount)


def scenario(
    url: str, body: Callable[[ApiHarness, httpx.AsyncClient], Awaitable[None]], **kw: Any
) -> None:
    async def go() -> None:
        h = build_harness(url, **kw)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=h.app), base_url="http://t"
        ) as c:
            try:
                await body(h, c)
            finally:
                await h.close()

    run_async(go)


def post(c: httpx.AsyncClient, key: str = "k", **over: Any) -> Any:
    return c.post("/audits", json={**BODY, **over}, headers={**HDR, "Idempotency-Key": key})


async def seed(
    h: ApiHarness,
    *,
    status: str = "complete",
    cost: float = 0.0,
    cap: float = 0.75,
    key_id: str = TEST_KEY_ID,
    when: datetime | None = None,
) -> str:
    async with h.session_factory() as s, s.begin():
        audit = Audit(
            idempotency_key=f"seed-{uuid.uuid4()}",
            api_key_id=uuid.UUID(key_id),
            request=BODY,
            brand="Acme",
            status=status,
            total_cost_usd=cost,
            cost_cap_usd=cap,
        )
        s.add(audit)
        await s.flush()
        if when is not None:
            await s.execute(
                text("UPDATE audits SET created_at = :t WHERE id = :i"), {"t": when, "i": audit.id}
            )
        return str(audit.id)


async def cap_of(h: ApiHarness, audit_id: str) -> float:
    async with h.session_factory() as s:
        return float(
            (
                await s.execute(select(Audit.cost_cap_usd).where(Audit.id == uuid.UUID(audit_id)))
            ).scalar_one()
        )


def test_without_a_budget_the_default_cap_applies(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        r = await post(c)
        assert r.status_code == 202 and await cap_of(h, r.json()["id"]) == 0.75
        await h.queue.drain()

    scenario(migrated_db_url, body, principal=budgeted(None))


def test_a_big_budget_does_not_raise_the_cap_above_the_default(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        r = await post(c)
        assert await cap_of(h, r.json()["id"]) == 0.75
        await h.queue.drain()

    scenario(migrated_db_url, body, principal=budgeted(50))


def test_a_small_budget_lowers_the_cap(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        r = await post(c)
        assert await cap_of(h, r.json()["id"]) == 0.5
        await h.queue.drain()

    scenario(migrated_db_url, body, principal=budgeted(0.5))


def test_money_already_spent_today_reduces_what_is_left(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        await seed(h, status="complete", cost=0.40)
        r = await post(c)
        assert r.status_code == 202 and await cap_of(h, r.json()["id"]) == 0.1
        await h.queue.drain()

    scenario(migrated_db_url, body, principal=budgeted(0.5))


def test_a_running_audit_reserves_its_full_cap_not_just_what_it_has_spent(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        await seed(h, status="running", cost=0.01, cap=0.75)  # has spent a cent but may spend 75
        r = await post(c)
        assert r.status_code == 202 and await cap_of(h, r.json()["id"]) == 0.25
        await h.queue.drain()

    scenario(migrated_db_url, body, principal=budgeted(1.0))


def test_a_finished_audit_counts_what_it_actually_spent(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        await seed(h, status="complete", cost=0.05, cap=0.75)  # cap was 75 cents, spend 5
        r = await post(c)
        assert await cap_of(h, r.json()["id"]) == 0.75  # 1.0 - 0.05 is plenty
        await h.queue.drain()

    scenario(migrated_db_url, body, principal=budgeted(1.0))


def test_an_exhausted_budget_is_429_with_a_clear_message_and_creates_nothing(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        await seed(h, status="complete", cost=1.0)
        r = await post(c)
        assert r.status_code == 429
        detail = r.json()["detail"]
        assert (
            "Daily cost budget of $1.00 reached" in detail
            and "$1.00 is already committed" in detail
        )
        assert "00:00 UTC" in detail and "running audits reserve their full cost cap" in detail
        assert 1 <= int(r.headers["retry-after"]) <= 86_400
        assert h.queue.enqueued == []
        async with h.session_factory() as s:
            assert len((await s.execute(select(Audit))).scalars().all()) == 1  # only the seeded one

    scenario(migrated_db_url, body, principal=budgeted(1.0))


def test_a_repeat_of_an_existing_audit_is_free_even_when_the_budget_is_gone(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        first = await post(c, "same")
        await h.queue.drain()
        await seed(h, status="complete", cost=5.0)  # now the budget is far exceeded
        assert (await post(c, "other")).status_code == 429
        again = await post(c, "same")
        assert again.status_code == 200 and again.json()["id"] == first.json()["id"]

    scenario(migrated_db_url, body, principal=budgeted(1.0))


def test_yesterdays_spend_does_not_count(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        yesterday = utc_day_start(datetime.now(UTC)) - timedelta(hours=1)
        await seed(h, status="complete", cost=5.0, when=yesterday)
        r = await post(c)
        assert r.status_code == 202 and await cap_of(h, r.json()["id"]) == 0.75
        await h.queue.drain()

    scenario(migrated_db_url, body, principal=budgeted(1.0))


def test_other_keys_spend_does_not_count(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        await seed(h, status="complete", cost=5.0, key_id=str(uuid.uuid4()))
        r = await post(c)
        assert r.status_code == 202
        await h.queue.drain()

    scenario(migrated_db_url, body, principal=budgeted(1.0))


def test_less_than_a_useful_remainder_is_refused(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        await seed(h, status="complete", cost=1.0 - MIN_USEFUL_CAP_USD / 2)
        assert (await post(c, "dust")).status_code == 429

    scenario(migrated_db_url, body, principal=budgeted(1.0))


def test_exactly_a_useful_remainder_is_allowed(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        await seed(h, status="complete", cost=0.99)
        r = await post(c, "exact")
        assert r.status_code == 202 and await cap_of(h, r.json()["id"]) == 0.01
        await h.queue.drain()

    scenario(migrated_db_url, body, principal=budgeted(1.0))


def test_parallel_requests_cannot_overspend_the_budget(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        h.app.state.settings = Settings(
            rate_limit_default_per_min=100_000, default_cost_cap_usd=0.5
        )
        h.queue.hold()
        responses = await asyncio.gather(*[post(c, f"race-{i}") for i in range(8)])
        codes = sorted(r.status_code for r in responses)
        assert codes == [202, 202] + [429] * 6  # budget 1.0 / cap 0.5 = exactly two audits
        async with h.session_factory() as s:
            caps = [float(x) for x in (await s.execute(select(Audit.cost_cap_usd))).scalars()]
        assert sum(caps) <= 1.0
        h.queue.release()
        await h.queue.drain()

    scenario(migrated_db_url, body, principal=budgeted(1.0))


def test_an_audit_with_a_squeezed_cap_stops_at_it_and_never_exceeds_the_budget(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        await seed(h, status="complete", cost=0.97)  # leaves 3 cents
        created = await post(c)
        assert await cap_of(h, created.json()["id"]) == 0.03
        await h.queue.drain()
        done = (await c.get(f"/audits/{created.json()['id']}", headers=HDR)).json()
        assert done["status"] == "capped" and "cost_cap" in done["caps_hit"]
        # each fake model call costs 1 cent, so the overshoot is at most the one call in flight
        assert done["summary"]["total_cost_usd"] <= 0.03 + 0.02

    scenario(migrated_db_url, body, principal=budgeted(1.0))


def test_a_failed_enqueue_gives_the_reservation_back(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        h.queue.fail_next(1)
        assert (await post(c, "k1")).status_code == 503
        r = await post(c, "k1")
        assert r.status_code == 202 and await cap_of(h, r.json()["id"]) == 0.75
        await h.queue.drain()

    scenario(migrated_db_url, body, principal=budgeted(1.0))
