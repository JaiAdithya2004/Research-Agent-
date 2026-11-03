"""Per-key request rate limit: a fixed one-minute window.

Redis in production (so every API process and worker shares the count), in memory for tests. If
the limiter itself is down the request is allowed and a warning is logged: the per-key daily
cost budget lives in Postgres and still bounds spend, so a Redis outage must not take the API
down with it.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import HTTPException, Response

from app.auth.authenticator import Principal

logger = logging.getLogger("app.ratelimit")

WINDOW_SECONDS = 60


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after_s: int


class RateLimiter(Protocol):
    async def hit(self, key: str, limit: int) -> RateDecision: ...


def _decision(count: int, limit: int, now: float) -> RateDecision:
    retry_after = max(1, WINDOW_SECONDS - int(now % WINDOW_SECONDS))
    return RateDecision(
        allowed=count <= limit,
        limit=limit,
        remaining=max(0, limit - count),
        retry_after_s=retry_after,
    )


class InMemoryRateLimiter:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._counts: dict[tuple[str, int], int] = {}

    async def hit(self, key: str, limit: int) -> RateDecision:
        now = self._clock()
        window = int(now // WINDOW_SECONDS)
        self._counts = {k: v for k, v in self._counts.items() if k[1] >= window}
        count = self._counts.get((key, window), 0) + 1
        self._counts[(key, window)] = count
        return _decision(count, limit, now)


# INCR and set the expiry atomically, so a crash between the two cannot leave a key that never
# expires.
_LUA = """
local c = redis.call('INCR', KEYS[1])
if c == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return c
"""


class RedisRateLimiter:
    def __init__(self, url: str, clock: Callable[[], float] = time.time) -> None:
        self._url = url
        self._clock = clock
        self._redis: Any = None
        self._script: Any = None

    async def _client(self) -> Any:
        if self._redis is None:
            from redis.asyncio import Redis

            self._redis = Redis.from_url(self._url)
            self._script = self._redis.register_script(_LUA)
        return self._redis

    async def hit(self, key: str, limit: int) -> RateDecision:
        await self._client()
        now = self._clock()
        window = int(now // WINDOW_SECONDS)
        count = int(await self._script(keys=[f"rl:{key}:{window}"], args=[WINDOW_SECONDS * 2]))
        return _decision(count, limit, now)

    async def aclose(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None


async def enforce(
    limiter: RateLimiter, principal: Principal, default_limit: int, response: Response
) -> None:
    limit = principal.rate_limit_per_min or default_limit
    try:
        decision = await limiter.hit(f"key:{principal.id}", limit)
    except Exception:
        logger.warning("rate_limiter_unavailable", exc_info=False)
        return  # fail open: the daily budget still bounds spend
    headers = {
        "X-RateLimit-Limit": str(decision.limit),
        "X-RateLimit-Remaining": str(decision.remaining),
    }
    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Rate limit exceeded: this API key may make {decision.limit} requests per "
                f"minute. Try again in {decision.retry_after_s} seconds."
            ),
            headers={**headers, "Retry-After": str(decision.retry_after_s)},
        )
    for name, value in headers.items():
        response.headers[name] = value
