"""Raw API keys never reach the logs. Two layers, each proven on its own:

1. the application never logs a credential (tests run with redaction UNINSTALLED);
2. the redaction safety net masks one anyway.
"""

import io
import json
import logging
from collections.abc import Iterator
from typing import Any

import httpx
import psycopg
import pytest

from app.auth.authenticator import Principal
from app.auth.keys import CreatedKey, generate_key
from app.config import Settings
from app.logging_config import (
    REDACTED,
    JsonFormatter,
    RedactingFormatter,
    bind_log_context,
    env_secrets,
    install_redaction,
    redact,
    reset_log_context,
    uninstall_redaction,
)
from app.ratelimit import InMemoryRateLimiter
from tests.api_support import TEST_KEY_ID, build_harness
from tests.conftest import run_async
from tests.test_orchestrator import QUESTIONS

Conn = psycopg.Connection[Any]
RAW_KEY = "ava_" + "Zq8Lm3Xv9Tb2Nc7Rk4Wd6Yh1Pf5Gs0Aj"


@pytest.fixture(autouse=True)
def _no_redaction() -> Iterator[None]:
    """Layer 1 must hold on its own, so these tests run with the safety net removed."""
    uninstall_redaction()
    yield
    uninstall_redaction()


# --- layer 2: redaction ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        f"key is {RAW_KEY} ok",
        f"Authorization: Bearer {RAW_KEY}",
        "authorization=Bearer abcdefghijklmnop1234",
        f"headers={{'X-API-Key': '{RAW_KEY}'}}",
        f'{{"x-api-key": "{RAW_KEY}"}}',
        "api_key=sk-abcdefghijklmnopqrstuvwxyz123456",
        "postgresql://app:Sup3rSecretPw@db.internal:5432/app",
        "redis://:Sup3rSecretPw@cache:6379/0",
        "AIzaSyA-abcdefghijklmnopqrstuvwxyz012345",
    ],
)
def test_credentials_are_masked(text: str) -> None:
    out = redact(text)
    for secret in (RAW_KEY, "abcdefghijklmnop1234", "Sup3rSecretPw", "sk-abcdefghijklmnop"):
        assert secret not in out
    assert "AIzaSyA-abcdefgh" not in out
    assert REDACTED in out


def test_redaction_is_idempotent_and_leaves_ordinary_text_alone() -> None:
    once = redact(f"hello {RAW_KEY}")
    assert redact(once) == once
    plain = "audit 123 finished; Acme mentioned 3 of 5 times (https://example.com/a)"
    assert redact(plain) == plain


def test_exact_secret_values_from_the_environment_are_masked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "plainlookingvalue-1234567")
    monkeypatch.setenv("SOME_FLAG", "plainlookingvalue-7654321")  # not a secret-named variable
    assert "plainlookingvalue-1234567" in env_secrets()
    out = redact("a plainlookingvalue-1234567 b plainlookingvalue-7654321")
    assert "plainlookingvalue-1234567" not in out and "plainlookingvalue-7654321" in out


def test_short_env_values_are_not_treated_as_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_TOKEN", "dev")
    assert redact("a developer devoted to dev") == "a developer devoted to dev"


def _capture(formatter: logging.Formatter) -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)
    logger = logging.getLogger(f"test.redact.{id(stream)}")
    logger.handlers[:] = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    return logger, stream


def test_installed_redaction_masks_messages_args_and_extra() -> None:
    install_redaction()
    install_redaction()  # idempotent
    logger, stream = _capture(logging.Formatter("%(message)s"))
    logger.info("got %s", RAW_KEY)
    logger.info(f"plain {RAW_KEY}")
    assert RAW_KEY not in stream.getvalue() and REDACTED in stream.getvalue()


def test_exception_text_is_masked_by_the_formatter() -> None:
    logger, stream = _capture(RedactingFormatter("%(message)s"))
    try:
        raise RuntimeError(f"upstream rejected {RAW_KEY}")
    except RuntimeError:
        logger.exception("boom")
    assert "Traceback" in stream.getvalue() and RAW_KEY not in stream.getvalue()


def test_json_formatter_emits_one_redacted_object_per_line_with_context_fields() -> None:
    install_redaction()
    logger, stream = _capture(JsonFormatter())
    token = bind_log_context(audit_id="a-1", question_id=2, model="m")
    try:
        logger.info(
            "fetched", extra={"url": "https://x.test/p", "latency_ms": 12, "cost_usd": 0.01}
        )
        logger.warning(f"leaky {RAW_KEY}", extra={"attempt": 2, "note": f"Bearer {RAW_KEY}"})
    finally:
        reset_log_context(token)
    lines = stream.getvalue().strip().splitlines()
    assert len(lines) == 2
    first, second = (json.loads(line) for line in lines)
    assert first["audit_id"] == "a-1" and first["question_id"] == 2 and first["model"] == "m"
    assert first["url"] == "https://x.test/p" and first["latency_ms"] == 12
    assert first["cost_usd"] == 0.01 and first["level"] == "INFO" and "ts" in first
    assert second["attempt"] == 2 and RAW_KEY not in stream.getvalue()


def test_log_context_does_not_leak_after_reset() -> None:
    install_redaction()
    logger, stream = _capture(JsonFormatter())
    token = bind_log_context(audit_id="gone")
    reset_log_context(token)
    logger.info("later")
    assert "audit_id" not in json.loads(stream.getvalue())


# --- secrets in objects' reprs --------------------------------------------------------------


def test_reprs_never_contain_secrets() -> None:
    settings = Settings(
        database_url="postgresql+psycopg://u:dbpassword99@h/d",
        redis_url="redis://:redispass99@h/0",
        api_key_pepper="pepper-value-abcdef",
        sentry_dsn="https://publickey@o1.ingest.sentry.io/1",
    )
    shown = repr(settings) + str(settings)
    for secret in ("dbpassword99", "redispass99", "pepper-value-abcdef", "publickey"):
        assert secret not in shown
    created = CreatedKey(id="i", raw_key=RAW_KEY, prefix="ava_Zq8L")
    assert RAW_KEY not in repr(created) and RAW_KEY not in str(created)
    assert RAW_KEY not in repr(Principal(id="i", name="n"))
    assert generate_key().startswith("ava_")


# --- layer 1: the application itself never logs the key (redaction is OFF here) ---------------


def _everything_logged(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(
        f"{r.getMessage()} {json.dumps(r.__dict__, default=str)}" for r in caplog.records
    )


BODY: dict[str, Any] = {
    "brand": "Acme",
    "category": "crm",
    "competitors": ["Rival"],
    "questions": QUESTIONS,
    "repeats": 1,
}


def test_the_app_never_logs_the_raw_key_on_any_path(
    migrated_db_url: str, db: Conn, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    async def go() -> None:
        h = build_harness(
            migrated_db_url,
            principals={RAW_KEY: Principal(id=TEST_KEY_ID, name="k", rate_limit_per_min=3)},
            rate_limiter=InMemoryRateLimiter(),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=h.app), base_url="http://t"
        ) as c:
            good = {"X-API-Key": RAW_KEY}
            created = await c.post("/audits", json=BODY, headers={**good, "Idempotency-Key": "k1"})
            assert created.status_code == 202
            await h.queue.drain()
            await c.get(f"/audits/{created.json()['id']}", headers=good)
            # wrong key that contains the real one as a prefix, and a malformed header
            assert (await c.get("/audits", headers={"X-API-Key": RAW_KEY + "x"})).status_code == 401
            assert (
                await c.get("/audits", headers={"X-API-Key": "ava_nonsense"})
            ).status_code == 401
            assert (await c.get("/audits")).status_code == 401
            # exceed the 3/min limit
            statuses = [(await c.get("/audits", headers=good)).status_code for _ in range(4)]
            assert 429 in statuses
            # an invalid body is echoed back in a 422; the key must not appear in any log
            bad = await c.post(
                "/audits", json={"brand": ""}, headers={**good, "Idempotency-Key": "k2"}
            )
            assert bad.status_code in (422, 429)
        await h.close()

    run_async(go)
    logged = _everything_logged(caplog)
    assert RAW_KEY not in logged
    assert RAW_KEY[4:] not in logged  # not even the secret part on its own


def test_the_app_never_logs_the_key_when_the_server_errors(
    migrated_db_url: str, db: Conn, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    async def go() -> None:
        h = build_harness(
            migrated_db_url, principals={RAW_KEY: Principal(id=TEST_KEY_ID, name="k")}
        )
        h.queue.fail_next(1)  # enqueue raises -> 503 path with exception handling
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=h.app), base_url="http://t"
        ) as c:
            r = await c.post(
                "/audits", json=BODY, headers={"X-API-Key": RAW_KEY, "Idempotency-Key": "boom"}
            )
            assert r.status_code == 503
            assert RAW_KEY not in r.text
        await h.close()

    run_async(go)
    assert RAW_KEY not in _everything_logged(caplog)
