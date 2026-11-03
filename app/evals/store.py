"""Persist eval results so the API (and the evals page) can show history."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.models import EvalRun
from app.evals.models import EvalResult


async def save_eval_result_with(
    session_factory: async_sessionmaker[AsyncSession], result: EvalResult
) -> str:
    async with session_factory() as session, session.begin():
        row = EvalRun(
            prompt_version=result.prompt_version,
            classifier_prompt_version=result.classifier_prompt_version,
            repeats=result.repeats,
            extractor_model=result.extractor_model,
            started_at=result.started_at,
            result=result.model_dump(mode="json"),
        )
        session.add(row)
        await session.flush()
        return str(row.id)


async def save_eval_result(database_url: str, result: EvalResult) -> str:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        return await save_eval_result_with(
            async_sessionmaker(engine, expire_on_commit=False), result
        )
    finally:
        await engine.dispose()


async def list_eval_runs(
    session_factory: async_sessionmaker[AsyncSession], limit: int = 100
) -> list[tuple[str, EvalResult]]:
    async with session_factory() as session:
        rows = (
            await session.execute(select(EvalRun).order_by(EvalRun.started_at.desc()).limit(limit))
        ).scalars()
        return [(str(r.id), EvalResult.model_validate(r.result)) for r in rows]
