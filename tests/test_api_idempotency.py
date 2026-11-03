"""POST /audits idempotency (SPEC criterion 4): the same Idempotency-Key returns the same audit
with zero new LLM, search or fetch calls. These run the real API over real Postgres, with fake
model and fetcher counters and an in-process queue so a created audit really executes."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import psycopg
import pytest
from sqlalchemy import func, select

from app.api.main import create_app
from app.db.models import Audit, Run
from tests.api_support import ApiHarness, build_harness
from tests.conftest import run_async

Conn = psycopg.Connection[Any]
KEY = {"X-API-Key": "test-key"}
BODY: dict[str, Any] = {
    "brand": "Acme",
    "category": "crm",
    "competitors": ["Rival"],
    "questions": ["Best CRM for startups?", "Best project tool?"],
    "repeats": 2,
    "search_mode": "grounded",
}


def hdr(idem: str | None = "k-1", **extra: str) -> dict[str, str]:
    headers = {**KEY, **extra}
    if idem is not None:
        headers["Idempotency-Key"] = idem
    return headers


def scenario(
    migrated_db_url: str, body: Callable[[ApiHarness, httpx.AsyncClient], Awaitable[None]]
) -> None:
    async def go() -> None:
        harness = build_harness(migrated_db_url)
        transport = httpx.ASGITransport(app=harness.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            try:
                await body(harness, client)
            finally:
                await harness.close()

    run_async(go)


async def count(harness: ApiHarness, model: Any) -> int:
    async with harness.session_factory() as s:
        return int((await s.execute(select(func.count()).select_from(model))).scalar_one())


# --- required header ------------------------------------------------------------------------------------


def test_missing_idempotency_key_is_422(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        r = await c.post("/audits", json=BODY, headers=hdr(None))
        assert r.status_code == 422
        assert "idempotency" in r.text.lower()
        assert await count(h, Audit) == 0

    scenario(migrated_db_url, body)


@pytest.mark.parametrize("bad", ["", "   ", "x" * 256])
def test_blank_or_oversized_idempotency_key_is_422(
    migrated_db_url: str, db: Conn, bad: str
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        r = await c.post("/audits", json=BODY, headers=hdr(bad))
        assert r.status_code == 422
        assert await count(h, Audit) == 0

    scenario(migrated_db_url, body)


# --- first request ---------------------------------------------------------------------------------------


def test_first_request_creates_the_audit_and_returns_202(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        r = await c.post("/audits", json=BODY, headers=hdr())
        assert r.status_code == 202
        data = r.json()
        uuid.UUID(data["id"])
        assert data["status"] == "running" and data["brand"] == "Acme"
        assert r.headers["location"] == f"/audits/{data['id']}"
        assert h.queue.enqueued == [data["id"]]
        assert await count(h, Audit) == 1

    scenario(migrated_db_url, body)


def test_the_audit_really_runs_after_the_202(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        created = (await c.post("/audits", json=BODY, headers=hdr())).json()
        await h.queue.drain()
        got = (await c.get(f"/audits/{created['id']}", headers=KEY)).json()
        assert got["status"] == "complete"
        assert got["summary"]["total_runs"] == 4
        assert await count(h, Run) == 4

    scenario(migrated_db_url, body)


# --- the repeat: the one that matters ---------------------------------------------------------------------


def test_repeat_returns_200_with_the_same_audit_and_no_new_calls(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        first = await c.post("/audits", json=BODY, headers=hdr())
        await h.queue.drain()
        calls = (len(h.llm.calls), len(h.fetcher.calls), h.llm.tracker.spent_usd)
        assert calls[0] > 0 and calls[1] > 0  # the first request really did the work

        second = await c.post("/audits", json=BODY, headers=hdr())
        await h.queue.drain()
        assert second.status_code == 200
        assert second.json()["id"] == first.json()["id"]
        assert second.json()["status"] == "complete"  # the stored audit, not a new one
        assert (len(h.llm.calls), len(h.fetcher.calls), h.llm.tracker.spent_usd) == calls
        assert h.queue.enqueued == [first.json()["id"]]  # nothing was enqueued again
        assert await count(h, Audit) == 1 and await count(h, Run) == 4

    scenario(migrated_db_url, body)


def test_many_repeats_never_add_calls_or_rows(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        await c.post("/audits", json=BODY, headers=hdr())
        await h.queue.drain()
        calls = len(h.llm.calls)
        statuses = {
            (await c.post("/audits", json=BODY, headers=hdr())).status_code for _ in range(5)
        }
        await h.queue.drain()
        assert statuses == {200}
        assert len(h.llm.calls) == calls and await count(h, Audit) == 1

    scenario(migrated_db_url, body)


def test_repeat_while_the_audit_is_still_running_returns_the_same_audit(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        h.queue.hold()  # the job is accepted but has not started yet
        first = await c.post("/audits", json=BODY, headers=hdr())
        second = await c.post("/audits", json=BODY, headers=hdr())
        assert (first.status_code, second.status_code) == (202, 200)
        assert second.json()["id"] == first.json()["id"] and second.json()["status"] == "running"
        assert h.queue.enqueued == [first.json()["id"]]
        h.queue.release()
        await h.queue.drain()

    scenario(migrated_db_url, body)


def test_repeat_response_has_the_same_shape_as_the_original(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        h.queue.hold()
        first = (await c.post("/audits", json=BODY, headers=hdr())).json()
        second = (await c.post("/audits", json=BODY, headers=hdr())).json()
        assert set(first) == set(second)
        assert first["id"] == second["id"] and first["request"] == second["request"]
        assert first["created_at"] == second["created_at"]
        h.queue.release()
        await h.queue.drain()

    scenario(migrated_db_url, body)


# --- same key, different request ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        {"brand": "Other"},
        {"repeats": 3},
        {"questions": ["Only one?"]},
        {"search_mode": "both"},
        {"competitors": ["Rival", "Globex"]},
    ],
)
def test_reusing_a_key_with_a_different_request_is_422_and_changes_nothing(
    migrated_db_url: str, db: Conn, change: dict[str, Any]
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        first = await c.post("/audits", json=BODY, headers=hdr())
        await h.queue.drain()
        calls = len(h.llm.calls)
        r = await c.post("/audits", json={**BODY, **change}, headers=hdr())
        await h.queue.drain()
        assert r.status_code == 422
        assert "idempotency" in r.text.lower()
        assert await count(h, Audit) == 1
        assert len(h.llm.calls) == calls
        assert h.queue.enqueued == [first.json()["id"]]

    scenario(migrated_db_url, body)


def test_defaults_do_not_count_as_a_different_request(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        minimal = {k: v for k, v in BODY.items() if k != "repeats"}
        explicit = {**minimal, "repeats": 3, "research_depth": "cited_pages", "aliases": []}
        assert (await c.post("/audits", json=minimal, headers=hdr())).status_code == 202
        assert (await c.post("/audits", json=explicit, headers=hdr())).status_code == 200

    scenario(migrated_db_url, body)


def test_whitespace_normalisation_does_not_break_a_repeat(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        assert (await c.post("/audits", json=BODY, headers=hdr())).status_code == 202
        padded = {**BODY, "brand": "  Acme  "}
        assert (await c.post("/audits", json=padded, headers=hdr())).status_code == 200

    scenario(migrated_db_url, body)


def test_different_keys_make_different_audits(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        a = await c.post("/audits", json=BODY, headers=hdr("k-a"))
        b = await c.post("/audits", json=BODY, headers=hdr("k-b"))
        assert (a.status_code, b.status_code) == (202, 202)
        assert a.json()["id"] != b.json()["id"] and await count(h, Audit) == 2
        await h.queue.drain()

    scenario(migrated_db_url, body)


# --- concurrency -----------------------------------------------------------------------------------------------------


def test_concurrent_identical_requests_create_exactly_one_audit_and_one_job(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        h.queue.hold()
        responses = await asyncio.gather(
            *[c.post("/audits", json=BODY, headers=hdr("race")) for _ in range(8)]
        )
        codes = sorted(r.status_code for r in responses)
        assert codes == [200] * 7 + [202]
        assert len({r.json()["id"] for r in responses}) == 1
        assert await count(h, Audit) == 1
        assert len(h.queue.enqueued) == 1
        h.queue.release()
        await h.queue.drain()

    scenario(migrated_db_url, body)


# --- validation and failure ----------------------------------------------------------------------------------------------


def test_an_invalid_body_is_422_and_does_not_consume_the_key(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        too_many = {**BODY, "questions": [f"q{i}" for i in range(11)]}
        r = await c.post("/audits", json=too_many, headers=hdr("reuse"))
        assert r.status_code == 422 and await count(h, Audit) == 0
        ok = await c.post("/audits", json=BODY, headers=hdr("reuse"))
        assert ok.status_code == 202  # the key was not burned by the invalid attempt
        await h.queue.drain()

    scenario(migrated_db_url, body)


@pytest.mark.parametrize(
    "bad",
    [
        {"brand": ""},
        {"repeats": 0},
        {"repeats": 6},
        {"search_mode": "web"},
        {"questions": []},
        {"research_depth": "deep"},
        {"surprise": 1},
    ],
)
def test_validation_errors_are_422(migrated_db_url: str, db: Conn, bad: dict[str, Any]) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        r = await c.post("/audits", json={**BODY, **bad}, headers=hdr())
        assert r.status_code == 422 and h.queue.enqueued == []

    scenario(migrated_db_url, body)


def test_if_the_queue_is_down_the_audit_is_rolled_back_so_a_retry_works(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        h.queue.fail_next(1)
        r = await c.post("/audits", json=BODY, headers=hdr("retry"))
        assert r.status_code == 503
        assert await count(h, Audit) == 0  # no orphan audit that nobody will ever run
        again = await c.post("/audits", json=BODY, headers=hdr("retry"))
        assert again.status_code == 202 and await count(h, Audit) == 1
        await h.queue.drain()

    scenario(migrated_db_url, body)


def test_the_response_never_contains_secrets_or_internal_fields(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        r = await c.post("/audits", json=BODY, headers=hdr("leak"))
        assert "test-key" not in r.text and "idempotency" not in r.text.lower()
        await h.queue.drain()

    scenario(migrated_db_url, body)


# --- auth -------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}, {"X-API-Key": ""}])
def test_a_missing_or_wrong_api_key_is_401_and_nothing_is_created(
    migrated_db_url: str, db: Conn, headers: dict[str, str]
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        r = await c.post("/audits", json=BODY, headers={**headers, "Idempotency-Key": "k"})
        assert r.status_code == 401
        assert await count(h, Audit) == 0 and h.queue.enqueued == []

    scenario(migrated_db_url, body)


def test_create_app_is_importable_without_a_database() -> None:
    assert create_app is not None
