"""API key material: generation, keyed hashing and storage.

A key is shown once, when it is created. Only an HMAC-SHA256 of it (keyed with a server-side
pepper) is stored, so a database leak does not reveal usable keys, and a key cannot be recovered
by anyone, including us. Keys are 256-bit random values, so a fast keyed hash is appropriate (a
slow password hash protects low-entropy secrets; these have no entropy problem).
"""

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import func

from app.db.models import ApiKey

KEY_PREFIX = "ava_"
PREFIX_LENGTH = 8  # shown in listings so a key can be recognised without revealing it


def generate_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(raw_key: str, pepper: str) -> str:
    if not pepper:
        raise ValueError("an API key pepper is required (set API_KEY_PEPPER)")
    return hmac.new(pepper.encode(), raw_key.strip().encode(), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class CreatedKey:
    id: str
    raw_key: str  # the only time it exists outside the caller's hands
    prefix: str

    def __repr__(self) -> str:  # never let a log line or traceback print the key
        return f"CreatedKey(id={self.id!r}, prefix={self.prefix!r}, raw_key=<hidden>)"


@dataclass(frozen=True)
class ApiKeyInfo:
    id: str
    name: str
    key_prefix: str
    daily_budget_usd: float | None
    rate_limit_per_min: int | None
    created_at: datetime
    last_used_at: datetime | None
    revoked: bool


async def create_api_key(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    name: str,
    pepper: str,
    daily_budget_usd: float | None = None,
    rate_limit_per_min: int | None = None,
    raw_key: str | None = None,
) -> CreatedKey:
    if not name.strip():
        raise ValueError("a key needs a name")
    if daily_budget_usd is not None and daily_budget_usd < 0:
        raise ValueError("the daily budget cannot be negative")
    if rate_limit_per_min is not None and rate_limit_per_min < 1:
        raise ValueError("the rate limit must be at least 1 request per minute")
    raw = raw_key or generate_key()
    async with session_factory() as session, session.begin():
        row = ApiKey(
            id=uuid.uuid4(),
            name=name.strip(),
            key_prefix=raw[:PREFIX_LENGTH],
            key_hash=hash_key(raw, pepper),
            daily_budget_usd=daily_budget_usd,
            rate_limit_per_min=rate_limit_per_min,
        )
        session.add(row)
        await session.flush()
        return CreatedKey(id=str(row.id), raw_key=raw, prefix=row.key_prefix)


async def revoke_api_key(session_factory: async_sessionmaker[AsyncSession], key_id: str) -> bool:
    """True if a live key was revoked; False if it was unknown or already revoked."""
    try:
        parsed = uuid.UUID(key_id)
    except ValueError:
        return False
    async with session_factory() as session, session.begin():
        result = await session.execute(
            update(ApiKey)
            .where(ApiKey.id == parsed, ApiKey.revoked_at.is_(None))
            .values(revoked_at=func.now())
        )
        return result.rowcount > 0  # type: ignore[attr-defined,no-any-return]


async def list_api_keys(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[ApiKeyInfo]:
    async with session_factory() as session:
        rows = (await session.execute(select(ApiKey).order_by(ApiKey.created_at))).scalars()
        return [
            ApiKeyInfo(
                id=str(r.id),
                name=r.name,
                key_prefix=r.key_prefix,
                daily_budget_usd=float(r.daily_budget_usd)
                if r.daily_budget_usd is not None
                else None,
                rate_limit_per_min=r.rate_limit_per_min,
                created_at=r.created_at,
                last_used_at=r.last_used_at,
                revoked=r.revoked_at is not None,
            )
            for r in rows
        ]
