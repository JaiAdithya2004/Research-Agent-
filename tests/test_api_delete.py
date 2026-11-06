"""DELETE /audits/{id}: a soft delete. The audit disappears from every read; its rows stay, because
raw answers, grounding metadata and reviews are never destroyed (SPEC criterion 9)."""

import uuid
from typing import Any

import httpx
import psycopg
from sqlalchemy import func, select

from app.db.models import Audit, Review, Run
from tests.api_support import ApiHarness
from tests.test_review_api import post, scenario, seed_audit, seed_run

Conn = psycopg.Connection[Any]
KEY = {"X-API-Key": "test-key"}


def test_deleting_requires_the_api_key(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        aid = await seed_audit(h)
        assert (await c.delete(f"/audits/{aid}")).status_code == 401
        assert (await c.get(f"/audits/{aid}", headers=KEY)).status_code == 200

    scenario(migrated_db_url, body)


def test_an_unknown_or_malformed_id_is_a_404(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        assert (await c.delete(f"/audits/{uuid.uuid4()}", headers=KEY)).status_code == 404
        assert (await c.delete("/audits/not-a-uuid", headers=KEY)).status_code == 404

    scenario(migrated_db_url, body)


def test_a_running_audit_cannot_be_deleted(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        aid = await seed_audit(h, status="running")
        r = await c.delete(f"/audits/{aid}", headers=KEY)
        assert r.status_code == 409
        assert (await c.get(f"/audits/{aid}", headers=KEY)).status_code == 200

    scenario(migrated_db_url, body)


def test_a_deleted_audit_is_gone_from_every_read(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        keep = await seed_audit(h, brand="Keep")
        gone = await seed_audit(h, brand="Gone")
        gone_run = await seed_run(h, gone, verified=False)  # flagged: sits in the review queue
        await seed_run(h, keep, verified=False)

        assert (await c.delete(f"/audits/{gone}", headers=KEY)).status_code == 204

        for path in (
            f"/audits/{gone}",
            f"/audits/{gone}/runs",
            f"/audits/{gone}/sources",
            f"/audits/{gone}/gaps",
            f"/audits/{gone}/events",
            f"/runs/{gone_run}",
        ):
            assert (await c.get(path, headers=KEY)).status_code == 404, path
        assert (await post(c, gone_run, action="approve")).status_code == 404
        assert (await c.delete(f"/audits/{gone}", headers=KEY)).status_code == 404  # already gone

        brands = [i["brand"] for i in (await c.get("/audits", headers=KEY)).json()["items"]]
        assert brands == ["Keep"]
        queue = (await c.get("/review/queue", headers=KEY)).json()["items"]
        assert [i["audit_id"] for i in queue] == [keep]

    scenario(migrated_db_url, body)


def test_deleting_keeps_the_stored_rows(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        aid = await seed_audit(h)
        run_id = await seed_run(h, aid, verified=False)
        assert (await post(c, run_id, action="approve")).status_code == 201
        assert (await c.delete(f"/audits/{aid}", headers=KEY)).status_code == 204
        async with h.session_factory() as s:
            audit = await s.get(Audit, uuid.UUID(aid))
            assert audit is not None and audit.deleted_at is not None
            assert (await s.execute(select(func.count()).select_from(Run))).scalar_one() == 1
            assert (await s.execute(select(func.count()).select_from(Review))).scalar_one() == 1

    scenario(migrated_db_url, body)
