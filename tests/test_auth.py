"""Hashed API keys: creation, authentication, revocation, the CLI and key-scoped idempotency."""

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import psycopg
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.main import create_app
from app.auth.authenticator import DbAuthenticator, Principal, StaticAuthenticator
from app.auth.keys import (
    KEY_PREFIX,
    create_api_key,
    generate_key,
    hash_key,
    list_api_keys,
    revoke_api_key,
)
from app.config import Settings
from app.db.models import ApiKey, Audit
from app.db.session import make_session_factory
from tests.conftest import run_async
from tests.test_orchestrator import QUESTIONS

Conn = psycopg.Connection[Any]
PEPPER = "test-pepper-0123456789"
BODY: dict[str, Any] = {
    "brand": "Acme",
    "category": "crm",
    "competitors": ["Rival"],
    "questions": QUESTIONS,
    "repeats": 1,
}


def with_db(url: str, body: Callable[[async_sessionmaker[Any]], Awaitable[None]]) -> None:
    async def go() -> None:
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            await body(make_session_factory(engine))
        finally:
            await engine.dispose()

    run_async(go)


# --- key material -----------------------------------------------------------------------------------


def test_generated_keys_are_long_random_and_prefixed() -> None:
    keys = {generate_key() for _ in range(50)}
    assert len(keys) == 50
    assert all(k.startswith(KEY_PREFIX) and len(k) >= 40 for k in keys)


def test_the_hash_is_keyed_deterministic_and_not_the_key() -> None:
    key = generate_key()
    assert hash_key(key, PEPPER) == hash_key(key, PEPPER)
    assert hash_key(key, PEPPER) != hash_key(key, "another-pepper")
    assert hash_key(key, PEPPER) != hash_key(generate_key(), PEPPER)
    assert key not in hash_key(key, PEPPER) and len(hash_key(key, PEPPER)) == 64


def test_an_empty_pepper_is_refused() -> None:
    with pytest.raises(ValueError):
        hash_key("ava_x", "")


# --- storage ----------------------------------------------------------------------------------------------


def test_the_raw_key_is_never_stored_only_its_hash(migrated_db_url: str, db: Conn) -> None:
    async def body(sf: async_sessionmaker[Any]) -> None:
        created = await create_api_key(
            sf, name="ci", pepper=PEPPER, daily_budget_usd=5, rate_limit_per_min=30
        )
        assert created.raw_key.startswith(KEY_PREFIX)
        async with sf() as s:
            row = (await s.execute(select(ApiKey))).scalar_one()
            assert row.key_hash == hash_key(created.raw_key, PEPPER)
            assert row.key_prefix == created.raw_key[:8]
            assert (row.name, float(row.daily_budget_usd or 0), row.rate_limit_per_min) == (
                "ci",
                5.0,
                30,
            )
            dump = " ".join(str(v) for v in vars(row).values())
            assert created.raw_key not in dump
            # and nowhere in the whole database
            tables = await s.execute(
                text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
            )
            for (table,) in tables.all():
                blob = (
                    await s.execute(text(f"SELECT string_agg(t::text, ' ') FROM \"{table}\" t"))
                ).scalar()
                assert created.raw_key not in (blob or ""), table

    with_db(migrated_db_url, body)


def test_key_names_must_be_non_blank_and_budgets_non_negative(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(sf: async_sessionmaker[Any]) -> None:
        for kw in (
            {"name": "  "},
            {"name": "x", "daily_budget_usd": -1},
            {"name": "x", "rate_limit_per_min": 0},
        ):
            with pytest.raises(ValueError):
                await create_api_key(sf, pepper=PEPPER, **kw)

    with_db(migrated_db_url, body)


def test_listing_shows_prefixes_not_keys(migrated_db_url: str, db: Conn) -> None:
    async def body(sf: async_sessionmaker[Any]) -> None:
        a = await create_api_key(sf, name="a", pepper=PEPPER)
        b = await create_api_key(sf, name="b", pepper=PEPPER)
        await revoke_api_key(sf, b.id)
        infos = {i.name: i for i in await list_api_keys(sf)}
        assert infos["a"].key_prefix == a.raw_key[:8] and infos["a"].revoked is False
        assert infos["b"].revoked is True
        assert a.raw_key not in repr(infos) and b.raw_key not in repr(infos)

    with_db(migrated_db_url, body)


# --- authentication -----------------------------------------------------------------------------------------


def test_a_valid_key_authenticates_to_its_principal(migrated_db_url: str, db: Conn) -> None:
    async def body(sf: async_sessionmaker[Any]) -> None:
        created = await create_api_key(
            sf, name="ci", pepper=PEPPER, daily_budget_usd=2.5, rate_limit_per_min=10
        )
        who = await DbAuthenticator(sf, PEPPER)(created.raw_key)
        assert who == Principal(
            id=created.id, name="ci", daily_budget_usd=2.5, rate_limit_per_min=10
        )

    with_db(migrated_db_url, body)


@pytest.mark.parametrize("bad", ["", "   ", "ava_" + "x" * 40, "wrong", "ava_", "a" * 5000])
def test_unknown_keys_do_not_authenticate(migrated_db_url: str, db: Conn, bad: str) -> None:
    async def body(sf: async_sessionmaker[Any]) -> None:
        await create_api_key(sf, name="real", pepper=PEPPER)
        assert await DbAuthenticator(sf, PEPPER)(bad) is None

    with_db(migrated_db_url, body)


def test_a_revoked_key_stops_working_immediately(migrated_db_url: str, db: Conn) -> None:
    async def body(sf: async_sessionmaker[Any]) -> None:
        created = await create_api_key(sf, name="ci", pepper=PEPPER)
        auth = DbAuthenticator(sf, PEPPER)
        assert await auth(created.raw_key) is not None
        assert await revoke_api_key(sf, created.id) is True
        assert await auth(created.raw_key) is None
        assert await revoke_api_key(sf, created.id) is False  # already revoked
        assert await revoke_api_key(sf, str(uuid.uuid4())) is False

    with_db(migrated_db_url, body)


def test_a_key_made_with_a_different_pepper_does_not_authenticate(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(sf: async_sessionmaker[Any]) -> None:
        created = await create_api_key(sf, name="ci", pepper=PEPPER)
        assert await DbAuthenticator(sf, "a-different-pepper")(created.raw_key) is None

    with_db(migrated_db_url, body)


def test_surrounding_whitespace_in_the_header_is_tolerated(migrated_db_url: str, db: Conn) -> None:
    async def body(sf: async_sessionmaker[Any]) -> None:
        created = await create_api_key(sf, name="ci", pepper=PEPPER)
        assert await DbAuthenticator(sf, PEPPER)(f"  {created.raw_key}\n") is not None

    with_db(migrated_db_url, body)


def test_last_used_is_recorded_but_not_on_every_request(migrated_db_url: str, db: Conn) -> None:
    async def body(sf: async_sessionmaker[Any]) -> None:
        created = await create_api_key(sf, name="ci", pepper=PEPPER)
        auth = DbAuthenticator(sf, PEPPER)
        await auth(created.raw_key)
        async with sf() as s:
            first = (await s.execute(select(ApiKey.last_used_at))).scalar_one()
        assert first is not None
        await auth(created.raw_key)
        async with sf() as s:
            assert (await s.execute(select(ApiKey.last_used_at))).scalar_one() == first  # throttled

    with_db(migrated_db_url, body)


def test_static_authenticator_for_tests() -> None:
    p = Principal(id=str(uuid.uuid4()), name="t")

    async def go() -> None:
        auth = StaticAuthenticator({"k": p})
        assert await auth("k") == p and await auth("other") is None and await auth("") is None

    run_async(go)


# --- through the API ---------------------------------------------------------------------------------------------


def api(sf: async_sessionmaker[Any], auth: Any) -> Any:
    from tests.test_orchestrator import FakeClient  # noqa: F401  (keeps test imports honest)

    class Queue:
        async def enqueue(self, audit_id: str) -> None:
            return None

    return create_app(
        settings=Settings(api_key_pepper=PEPPER),
        session_factory=sf,
        queue=Queue(),
        authenticator=auth,
    )


def test_the_api_accepts_a_database_key_and_rejects_revoked_or_unknown_ones(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(sf: async_sessionmaker[Any]) -> None:
        created = await create_api_key(sf, name="ci", pepper=PEPPER)
        app = api(sf, DbAuthenticator(sf, PEPPER))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            ok = await c.get("/audits", headers={"X-API-Key": created.raw_key})
            assert ok.status_code == 200 and created.raw_key not in ok.text
            assert (await c.get("/audits", headers={"X-API-Key": "nope"})).status_code == 401
            assert (await c.get("/audits")).status_code == 401
            await revoke_api_key(sf, created.id)
            assert (
                await c.get("/audits", headers={"X-API-Key": created.raw_key})
            ).status_code == 401

    with_db(migrated_db_url, body)


def test_a_401_never_says_which_part_was_wrong(migrated_db_url: str, db: Conn) -> None:
    async def body(sf: async_sessionmaker[Any]) -> None:
        created = await create_api_key(sf, name="ci", pepper=PEPPER)
        await revoke_api_key(sf, created.id)
        app = api(sf, DbAuthenticator(sf, PEPPER))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            bodies = {
                (await c.get("/audits", headers={"X-API-Key": k})).text
                for k in (created.raw_key, "ava_unknown_key_value_1234567890", "")
            }
            assert len(bodies) == 1  # revoked, unknown and missing are indistinguishable

    with_db(migrated_db_url, body)


def test_the_same_idempotency_key_from_two_api_keys_makes_two_separate_audits(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(sf: async_sessionmaker[Any]) -> None:
        a = await create_api_key(sf, name="a", pepper=PEPPER)
        b = await create_api_key(sf, name="b", pepper=PEPPER)
        app = api(sf, DbAuthenticator(sf, PEPPER))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:

            def hdr(k: str) -> dict[str, str]:
                return {"X-API-Key": k, "Idempotency-Key": "shared-key"}

            first = await c.post("/audits", json=BODY, headers=hdr(a.raw_key))
            other = await c.post(
                "/audits", json={**BODY, "brand": "Different"}, headers=hdr(b.raw_key)
            )
            again = await c.post("/audits", json=BODY, headers=hdr(a.raw_key))
            assert (first.status_code, other.status_code, again.status_code) == (202, 202, 200)
            assert (
                first.json()["id"] != other.json()["id"]
            )  # B did not get A's audit (or a 422 about it)
            assert again.json()["id"] == first.json()["id"]
        async with sf() as s:
            rows = (await s.execute(select(Audit.api_key_id))).scalars().all()
            assert {str(r) for r in rows} == {a.id, b.id}

    with_db(migrated_db_url, body)


# --- the CLI --------------------------------------------------------------------------------------------------------


def test_cli_create_prints_the_key_once_then_list_and_revoke(
    migrated_db_url: str,
    db: Conn,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.auth.__main__ import main

    monkeypatch.setenv("DATABASE_URL", migrated_db_url)
    monkeypatch.setenv("API_KEY_PEPPER", PEPPER)
    assert main(["create", "--name", "laptop", "--daily-budget", "3", "--rate-limit", "20"]) == 0
    out = capsys.readouterr().out
    key = next(line.split()[-1] for line in out.splitlines() if KEY_PREFIX in line)
    assert key.startswith(KEY_PREFIX) and "will not be shown again" in out.lower()

    assert main(["list"]) == 0
    listing = capsys.readouterr().out
    assert "laptop" in listing and key[:8] in listing and key not in listing

    key_id = next(line.split()[0] for line in listing.splitlines() if "laptop" in line)
    assert main(["revoke", "--id", key_id]) == 0
    assert "revoked" in capsys.readouterr().out.lower()
    assert main(["revoke", "--id", key_id]) == 1  # already revoked
    assert main(["create", "--name", "x", "--daily-budget", "-2"]) == 2


def test_cli_refuses_to_run_without_a_pepper(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.auth.__main__ import main

    monkeypatch.delenv("API_KEY_PEPPER", raising=False)
    assert main(["create", "--name", "x"]) == 2
    assert "API_KEY_PEPPER" in capsys.readouterr().err
