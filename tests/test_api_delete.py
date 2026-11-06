"""DELETE /audits/{id} removes the audit and everything that belongs to it.

Reviews are append-only and raw answers are write-once, so nothing else may delete them: the
database only allows it inside this one operation (a transaction-local flag)."""

import uuid
from typing import Any

import httpx
import psycopg
import pytest
from sqlalchemy import func, select, text

from app.db.models import Audit, AuditEvent, Citation, Review, Run
from app.db.models import SourceEvidence as EvidenceRow
from tests.api_support import ApiHarness
from tests.test_review_api import post, scenario, seed_audit, seed_run

Conn = psycopg.Connection[Any]
KEY = {"X-API-Key": "test-key"}


async def count(h: ApiHarness, model: Any) -> int:
    async with h.session_factory() as s:
        return int((await s.execute(select(func.count()).select_from(model))).scalar_one())


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
        assert (await c.delete(f"/audits/{aid}", headers=KEY)).status_code == 409
        assert (await c.get(f"/audits/{aid}", headers=KEY)).status_code == 200

    scenario(migrated_db_url, body)


def test_a_deleted_audit_and_all_its_data_are_gone(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        keep = await seed_audit(h, brand="Keep")
        gone = await seed_audit(h, brand="Gone")
        keep_run = await seed_run(h, keep, verified=False, urls=("https://g2.com/k",))
        gone_run = await seed_run(h, gone, verified=False, urls=("https://g2.com/x",))
        assert (await post(c, gone_run, action="approve")).status_code == 201
        assert (await post(c, keep_run, action="approve")).status_code == 201
        async with h.session_factory() as s, s.begin():
            s.add(AuditEvent(audit_id=uuid.UUID(gone), seq=1, type="x", data={}))
            s.add(AuditEvent(audit_id=uuid.UUID(keep), seq=1, type="x", data={}))

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
        assert (await c.delete(f"/audits/{gone}", headers=KEY)).status_code == 404

        brands = [i["brand"] for i in (await c.get("/audits", headers=KEY)).json()["items"]]
        assert brands == ["Keep"]

        # Every row of the deleted audit is really gone; the other audit is untouched.
        for model in (Audit, Run, Citation, Review, AuditEvent):
            assert await count(h, model) == 1, model.__name__
        async with h.session_factory() as s:
            assert (
                await s.execute(select(func.count()).select_from(EvidenceRow))
            ).scalar_one() == 0
            assert (await s.execute(select(Run.audit_id))).scalar_one() == uuid.UUID(keep)

    scenario(migrated_db_url, body)


def test_reviews_still_cannot_be_edited_or_deleted_by_anything_else(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        aid = await seed_audit(h)
        run_id = await seed_run(h, aid, verified=False)
        assert (await post(c, run_id, action="approve")).status_code == 201
        for statement in ("UPDATE reviews SET reason = 'edited'", "DELETE FROM reviews"):
            with pytest.raises(Exception, match="append-only"):
                async with h.session_factory() as s, s.begin():
                    await s.execute(text(statement))
        # The flag is local to one transaction: it does not leak into the next one.
        async with h.session_factory() as s, s.begin():
            await s.execute(text("SELECT set_config('app.audit_purge', 'on', true)"))
        with pytest.raises(Exception, match="append-only"):
            async with h.session_factory() as s, s.begin():
                await s.execute(text("DELETE FROM reviews"))
        assert await count(h, Review) == 1

    scenario(migrated_db_url, body)
