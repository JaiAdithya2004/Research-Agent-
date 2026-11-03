import os

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.envfile import load_env_file

DEFAULT_DATABASE_URL = "postgresql+psycopg://audit:audit@localhost:5432/audit"


def get_database_url() -> str:
    load_env_file()  # so alembic and scripts see .env too
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def make_engine(url: str | None = None) -> AsyncEngine:
    return create_async_engine(url or get_database_url())


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
