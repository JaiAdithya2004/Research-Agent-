"""Per-key rate limit: fixed window, Redis-backed in production, fail-open if Redis is down."""

import asyncio
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import psycopg
import pytest
from fastapi import HTTPException
from redis.asyncio import Redis

from app.auth.authenticator import Principal
from app.config import Settings
from app.ratelimit import InMemoryRateLimiter, RateDecision, RedisRateLimiter, enforce
from tests.api_support import ApiHarness, build_harness
from tests.conftest import run_async

Conn = psycopg.Connection[Any]
REDIS_URL = f"redis://localhost:6379/{os.environ.get('TEST_REDIS_DB', '15')}"
HDR = {"X-API-Key": "test-key"}


class Clock:
    def __init__(self, start: float = 1_000_040.0) -> None:  # 20 seconds into a minute
        self.now = start

    def __call__(self) -> float:
        return self.now


def principal(limit: int | None, name: str = "p") -> Principal:
    return Principal(id=str(uuid.uuid4()), name=name, rate_limit_per_min=limit)


# --- the in-memory limiter ------------------------------------------------------------------------------


def test_requests_within_the_limit_are_allowed_and_counted() -> None:
    async def go() -> None:
        limiter = InMemoryRateLimiter(Clock())
        decisions = [await limiter.hit("k", 3) for _ in range(3)]
        assert [d.allowed for d in decisions] == [True, True, True]
        assert [d.remaining for d in decisions] == [2, 1, 0]

    run_async(go)


def test_the_request_over_the_limit_is_refused_with_a_retry_time() -> None:
    async def go() -> None:
        clock = Clock(1_000_040.0)  # 20 s into the window: 40 s left
        limiter = InMemoryRateLimiter(clock)
        for _ in range(3):
            await limiter.hit("k", 3)
        over = await limiter.hit("k", 3)
        assert over == RateDecision(allowed=False, limit=3, remaining=0, retry_after_s=40)

    run_async(go)


def test_the_window_resets_and_keys_are_independent() -> None:
    async def go() -> None:
        clock = Clock()
        limiter = InMemoryRateLimiter(clock)
        for _ in range(3):
            await limiter.hit("a", 2)
        assert (await limiter.hit("a", 2)).allowed is False
        assert (await limiter.hit("b", 2)).allowed is True  # another key is unaffected
        clock.now += 60
        assert (await limiter.hit("a", 2)).allowed is True  # a new minute

    run_async(go)


# --- the Redis limiter -------------------------------------------------------------------------------------


@pytest.fixture
def clean_redis() -> Any:
    async def flush() -> None:
        r = Redis.from_url(REDIS_URL)
        try:
            await r.flushdb()
        finally:
            await r.aclose()

    run_async(flush)
    yield
    run_async(flush)


def test_redis_limiter_counts_atomically_across_concurrent_requests(clean_redis: None) -> None:
    async def go() -> None:
        limiter = RedisRateLimiter(REDIS_URL, Clock())
        try:
            decisions = await asyncio.gather(*[limiter.hit("k", 30) for _ in range(100)])
        finally:
            await limiter.aclose()
        assert sum(d.allowed for d in decisions) == 30  # exactly the limit, never more

    run_async(go)


def test_redis_counters_expire(clean_redis: None) -> None:
    async def go() -> None:
        limiter = RedisRateLimiter(REDIS_URL, Clock(1_000_040.0))
        await limiter.hit("k", 5)
        await limiter.aclose()
        r = Redis.from_url(REDIS_URL)
        try:
            keys = [k async for k in r.scan_iter("rl:*")]
            assert len(keys) == 1
            ttl = await r.ttl(keys[0])
            assert 0 < ttl <= 120  # so abandoned windows do not accumulate forever
        finally:
            await r.aclose()

    run_async(go)


def test_redis_limiter_is_shared_between_instances(clean_redis: None) -> None:
    async def go() -> None:
        clock = Clock()
        a, b = (
            RedisRateLimiter(REDIS_URL, clock),
            RedisRateLimiter(REDIS_URL, clock),
        )  # two API processes
        try:
            await a.hit("k", 2)
            await b.hit("k", 2)
            assert (await a.hit("k", 2)).allowed is False
        finally:
            await a.aclose()
            await b.aclose()

    run_async(go)


# --- enforce(): limits, headers, failure handling ---------------------------------------------------------------


class _Resp:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}


def test_enforce_uses_the_keys_own_limit_else_the_default() -> None:
    async def go() -> None:
        limiter = InMemoryRateLimiter(Clock())
        custom, plain = principal(2), principal(None)
        for _ in range(2):
            await enforce(limiter, custom, 100, _Resp())  # type: ignore[arg-type]
        with pytest.raises(HTTPException) as err:
            await enforce(limiter, custom, 100, _Resp())  # type: ignore[arg-type]
        assert err.value.status_code == 429
        for _ in range(100):
            await enforce(limiter, plain, 100, _Resp())  # type: ignore[arg-type]
        with pytest.raises(HTTPException):
            await enforce(limiter, plain, 100, _Resp())  # type: ignore[arg-type]

    run_async(go)


def test_a_broken_limiter_fails_open_and_says_so(caplog: pytest.LogCaptureFixture) -> None:
    class Broken:
        async def hit(self, key: str, limit: int) -> RateDecision:
            raise ConnectionError("redis is down")

    async def go() -> None:
        with caplog.at_level(logging.WARNING, logger="app.ratelimit"):
            await enforce(Broken(), principal(1), 1, _Resp())  # type: ignore[arg-type]

    run_async(go)
    assert any("rate_limiter_unavailable" in r.getMessage() for r in caplog.records)


def test_a_redis_that_is_unreachable_fails_open() -> None:
    async def go() -> None:
        limiter = RedisRateLimiter("redis://127.0.0.1:1/0")
        await enforce(limiter, principal(1), 1, _Resp())  # type: ignore[arg-type]
        await limiter.aclose()

    run_async(go)


# --- through the API ------------------------------------------------------------------------------------------------


def scenario(
    url: str, body: Callable[[ApiHarness, httpx.AsyncClient], Awaitable[None]], **kw: Any
) -> None:
    async def go() -> None:
        h = build_harness(url, **kw)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=h.app), base_url="http://t"
        ) as c:
            try:
                await body(h, c)
            finally:
                await h.close()

    run_async(go)


def test_the_request_over_the_limit_gets_429_with_a_clear_message(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        codes = [(await c.get("/audits", headers=HDR)).status_code for _ in range(3)]
        assert codes == [200, 200, 429]
        r = await c.get("/audits", headers=HDR)
        assert r.status_code == 429
        assert (
            "Rate limit exceeded" in r.json()["detail"]
            and "2 requests per minute" in r.json()["detail"]
        )
        assert 1 <= int(r.headers["retry-after"]) <= 60
        assert r.headers["x-ratelimit-limit"] == "2" and r.headers["x-ratelimit-remaining"] == "0"

    scenario(
        migrated_db_url, body, principal=principal(2), rate_limiter=InMemoryRateLimiter(Clock())
    )


def test_successful_responses_report_the_remaining_allowance(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        first = await c.get("/audits", headers=HDR)
        second = await c.get("/audits", headers=HDR)
        assert (first.headers["x-ratelimit-limit"], first.headers["x-ratelimit-remaining"]) == (
            "5",
            "4",
        )
        assert second.headers["x-ratelimit-remaining"] == "3"

    scenario(
        migrated_db_url, body, principal=principal(5), rate_limiter=InMemoryRateLimiter(Clock())
    )


def test_keys_are_limited_independently(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        for _ in range(3):
            await c.get("/audits", headers={"X-API-Key": "one"})
        assert (await c.get("/audits", headers={"X-API-Key": "one"})).status_code == 429
        assert (await c.get("/audits", headers={"X-API-Key": "two"})).status_code == 200

    scenario(
        migrated_db_url,
        body,
        principals={"one": principal(3, "one"), "two": principal(3, "two")},
        rate_limiter=InMemoryRateLimiter(Clock()),
    )


def test_the_default_limit_applies_to_keys_without_their_own(
    migrated_db_url: str, db: Conn
) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        h.app.state.settings = Settings(rate_limit_default_per_min=2)
        codes = [(await c.get("/audits", headers=HDR)).status_code for _ in range(3)]
        assert codes == [200, 200, 429]

    scenario(
        migrated_db_url, body, principal=principal(None), rate_limiter=InMemoryRateLimiter(Clock())
    )


def test_bad_keys_cannot_use_up_a_real_keys_allowance(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        for _ in range(10):
            assert (await c.get("/audits", headers={"X-API-Key": "wrong"})).status_code == 401
        assert (await c.get("/audits", headers=HDR)).status_code == 200

    scenario(
        migrated_db_url, body, principal=principal(2), rate_limiter=InMemoryRateLimiter(Clock())
    )


def test_health_is_never_rate_limited(migrated_db_url: str, db: Conn) -> None:
    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        codes = [(await c.get("/health")).status_code for _ in range(10)]
        assert codes == [200] * 10

    scenario(
        migrated_db_url, body, principal=principal(1), rate_limiter=InMemoryRateLimiter(Clock())
    )


def test_the_window_reopens_after_a_minute(migrated_db_url: str, db: Conn) -> None:
    clock = Clock()

    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        await c.get("/audits", headers=HDR)
        assert (await c.get("/audits", headers=HDR)).status_code == 429
        clock.now += 60
        assert (await c.get("/audits", headers=HDR)).status_code == 200

    scenario(migrated_db_url, body, principal=principal(1), rate_limiter=InMemoryRateLimiter(clock))


def test_a_down_limiter_does_not_take_the_api_down(migrated_db_url: str, db: Conn) -> None:
    class Broken:
        async def hit(self, key: str, limit: int) -> RateDecision:
            raise ConnectionError("redis is down")

    async def body(h: ApiHarness, c: httpx.AsyncClient) -> None:
        assert (await c.get("/audits", headers=HDR)).status_code == 200

    scenario(migrated_db_url, body, rate_limiter=Broken())
