"""Shared fixtures. DB tests need the compose Postgres (`docker compose up -d --wait`)."""

import asyncio
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable, Iterator
from typing import Any, TypeVar

import psycopg
import pytest
from psycopg import sql

T = TypeVar("T")

ADMIN_URL = os.environ.get("TEST_ADMIN_URL", "postgresql://audit:audit@localhost:5432/postgres")
TEST_DB = "audit_test"
TEST_SQLA_URL = f"postgresql+psycopg://audit:audit@localhost:5432/{TEST_DB}"
TABLES = (
    "reviews",
    "source_evidence",
    "citations",
    "runs",
    "page_snapshots",
    "audits",
    "eval_runs",
)


def run_async(fn: Callable[[], Awaitable[T]]) -> T:
    """Run a coroutine on a selector loop (psycopg async cannot use Windows' Proactor loop)."""
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        return runner.run(_wrap(fn))


async def _wrap(fn: Callable[[], Awaitable[T]]) -> T:
    return await fn()


@pytest.fixture(scope="session")
def migrated_db_url() -> Iterator[str]:
    """Create a fresh audit_test database and run `alembic upgrade head` against it."""
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(TEST_DB))
        )
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TEST_DB)))
    env = {**os.environ, "DATABASE_URL": TEST_SQLA_URL}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], check=True, env=env, cwd=os.getcwd()
    )
    yield TEST_SQLA_URL


@pytest.fixture
def db(migrated_db_url: str) -> Iterator[psycopg.Connection[Any]]:
    """Autocommit sync connection to the migrated DB, with tables emptied before each test."""
    plain_url = migrated_db_url.replace("+psycopg", "")
    with psycopg.connect(plain_url, autocommit=True) as conn:
        # Triggers forbid DELETE on append-only tables, so TRUNCATE (not row-level) resets them.
        conn.execute("TRUNCATE " + ", ".join(TABLES) + " CASCADE")
        yield conn
