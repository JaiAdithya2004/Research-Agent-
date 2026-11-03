import asyncio

from alembic import context
from sqlalchemy.engine import Connection

from app.db.models import Base
from app.db.session import get_database_url, make_engine

config = context.config
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_online() -> None:
    engine = make_engine()
    async with engine.connect() as connection:
        await connection.run_sync(_do_run)
    await engine.dispose()


def run_migrations_online() -> None:
    # psycopg's async mode cannot use the Windows default ProactorEventLoop.
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_run_online())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
