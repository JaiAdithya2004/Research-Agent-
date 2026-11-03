"""Eval results stored in Postgres and served by GET /evals/runs."""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import psycopg
import pytest

from app.evals.store import save_eval_result
from tests.api_support import ApiHarness, build_harness
from tests.conftest import run_async
from tests.test_evals import synthetic

Conn = psycopg.Connection[Any]
KEY = {"X-API-Key": "test-key"}


def result(version: str, when: datetime, **values: list[float]) -> Any:
    r = synthetic(version, **values)
    return r.model_copy(update={"started_at": when})


def scenario(url: str, body: Any) -> None:
    async def go() -> None:
        harness = build_harness(url)
        transport = httpx.ASGITransport(app=harness.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            try:
                await body(harness, client)
            finally:
                await harness.close()

    run_async(go)


def test_saved_runs_are_listed_newest_first_with_metrics(migrated_db_url: str, db: Conn) -> None:
    t0 = datetime(2026, 11, 1, tzinfo=UTC)

    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        await save_eval_result(
            migrated_db_url,
            result("v1", t0, extraction_accuracy=[0.8, 0.9], total_cost_usd=[0.1, 0.1]),
        )
        await save_eval_result(
            migrated_db_url,
            result("v2", t0 + timedelta(days=1), extraction_accuracy=[0.95], total_cost_usd=[0.12]),
        )
        r = await c.get("/evals/runs", headers=KEY)
        assert r.status_code == 200
        runs = r.json()
        assert [x["prompt_version"] for x in runs] == ["v2", "v1"]
        v1 = runs[1]
        assert v1["repeats"] == 2 and v1["id"]
        assert v1["metrics"]["extraction_accuracy"]["mean"] == pytest.approx(0.85)
        assert v1["metrics"]["extraction_accuracy"]["values"] == [0.8, 0.9]
        assert v1["counts"] == {"answers": 18, "pages": 12, "injection": 8}

    scenario(migrated_db_url, body)


def test_the_limit_parameter_is_honoured_and_validated(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        for i in range(3):
            await save_eval_result(
                migrated_db_url,
                result(f"v{i}", datetime(2026, 11, 1 + i, tzinfo=UTC), extraction_accuracy=[0.5]),
            )
        assert len((await c.get("/evals/runs", params={"limit": 2}, headers=KEY)).json()) == 2
        assert (await c.get("/evals/runs", params={"limit": 0}, headers=KEY)).status_code == 422
        assert (await c.get("/evals/runs", params={"limit": 501}, headers=KEY)).status_code == 422

    scenario(migrated_db_url, body)


def test_no_runs_is_an_empty_list_and_a_key_is_required(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        assert (await c.get("/evals/runs", headers=KEY)).json() == []
        assert (await c.get("/evals/runs")).status_code == 401

    scenario(migrated_db_url, body)


def test_saving_is_append_only_history_not_an_upsert(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        when = datetime(2026, 11, 1, tzinfo=UTC)
        for _ in range(2):
            await save_eval_result(migrated_db_url, result("v1", when, extraction_accuracy=[0.9]))
        runs = (await c.get("/evals/runs", headers=KEY)).json()
        assert len(runs) == 2 and runs[0]["id"] != runs[1]["id"]

    scenario(migrated_db_url, body)
