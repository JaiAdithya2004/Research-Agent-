"""Turn a raw API key into a Principal (or nothing)."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.keys import hash_key
from app.db.models import ApiKey

LAST_USED_RESOLUTION = timedelta(minutes=5)  # do not write on every request


@dataclass(frozen=True)
class Principal:
    """Who is calling. The name is recorded as the reviewer on review actions."""

    id: str  # the api_keys row id, a UUID
    name: str
    daily_budget_usd: float | None = None
    rate_limit_per_min: int | None = None


class Authenticator(Protocol):
    async def __call__(self, raw_key: str) -> Principal | None: ...


class DbAuthenticator:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], pepper: str) -> None:
        self._sf = session_factory
        self._pepper = pepper

    async def __call__(self, raw_key: str) -> Principal | None:
        key = raw_key.strip()
        if not key or len(key) > 512:
            return None
        digest = hash_key(key, self._pepper)
        async with self._sf() as session:
            row = (
                await session.execute(
                    select(ApiKey).where(ApiKey.key_hash == digest, ApiKey.revoked_at.is_(None))
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            principal = Principal(
                id=str(row.id),
                name=row.name,
                daily_budget_usd=float(row.daily_budget_usd)
                if row.daily_budget_usd is not None
                else None,
                rate_limit_per_min=row.rate_limit_per_min,
            )
            now = datetime.now(UTC)
            if row.last_used_at is None or now - row.last_used_at > LAST_USED_RESOLUTION:
                await session.execute(
                    update(ApiKey).where(ApiKey.id == row.id).values(last_used_at=now)
                )
                await session.commit()
            return principal


class StaticAuthenticator:
    """For tests: a fixed mapping of raw key to principal."""

    def __init__(self, principals: dict[str, Principal]) -> None:
        self._principals = principals

    async def __call__(self, raw_key: str) -> Principal | None:
        return self._principals.get(raw_key.strip()) if raw_key.strip() else None
