"""Checking an OpenRouter key: GET /api/v1/key. No network; the key never appears in output."""

import io
from contextlib import redirect_stderr, redirect_stdout

import httpx
import pytest

from app.llm import check_key
from app.llm.providers import KeyCheck, check_openrouter_key
from tests.conftest import run_async

SECRET = "sk-or-v1-" + "a1b2c3d4e5f6" * 4


def client(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler)


def check(status: int, body: object, seen: list[httpx.Request] | None = None) -> KeyCheck:
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=body)

    return run_async(lambda: check_openrouter_key(SECRET, http=client(httpx.MockTransport(handle))))


def test_a_valid_key_reports_label_and_remaining_credit() -> None:
    seen: list[httpx.Request] = []
    result = check(
        200, {"data": {"label": "audit", "limit": 20, "limit_remaining": 19.5, "usage": 0.5}}, seen
    )
    assert result.status == "valid" and result.label == "audit"
    assert result.limit_remaining == 19.5 and result.usage == 0.5
    assert seen[0].url == "https://openrouter.ai/api/v1/key"
    assert seen[0].headers["authorization"] == f"Bearer {SECRET}"


def test_a_key_with_no_limit_is_valid() -> None:
    result = check(200, {"data": {"label": "x", "limit": None, "limit_remaining": None}})
    assert result.status == "valid" and result.limit_remaining is None


def test_an_unwrapped_response_is_also_understood() -> None:
    assert check(200, {"label": "x", "usage": 1.0}).status == "valid"


def test_a_key_with_no_credit_left_is_flagged() -> None:
    result = check(200, {"data": {"label": "x", "limit_remaining": 0}})
    assert result.status == "no_credit"


def test_a_rejected_key_is_invalid() -> None:
    assert check(401, {"error": {"message": "No auth credentials found"}}).status == "invalid"


def test_other_http_errors_are_unknown_not_invalid() -> None:
    assert check(500, {"error": "boom"}).status == "unavailable"
    assert check(429, {"error": "slow down"}).status == "unavailable"


def test_a_garbage_body_is_unavailable() -> None:
    assert check(200, ["not", "an", "object"]).status == "unavailable"


def test_network_failure_is_unavailable() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    result = run_async(lambda: check_openrouter_key(SECRET, http=client(httpx.MockTransport(boom))))
    assert result.status == "unavailable"
    assert SECRET not in repr(result) and SECRET not in result.message


def test_an_empty_key_is_invalid_without_a_request() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    result = run_async(lambda: check_openrouter_key("  ", http=client(httpx.MockTransport(handle))))
    assert result.status == "invalid" and seen == []


def test_the_cli_never_prints_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET)

    async def fake(key: str, **_: object) -> KeyCheck:
        return KeyCheck(status="valid", message=f"rejected {key}", label="l", limit_remaining=3.0)

    monkeypatch.setitem(check_key.PROVIDERS, "openrouter", ("OPENROUTER_API_KEY", fake))
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = check_key.main(["openrouter"])
    assert code == 0 and SECRET not in out.getvalue() + err.getvalue()
    assert "valid" in out.getvalue()


def test_the_cli_fails_when_the_key_is_missing_or_bad(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    err = io.StringIO()
    with redirect_stderr(err):
        assert check_key.main(["openrouter"]) == 2
    assert "OPENROUTER_API_KEY" in err.getvalue()

    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET)

    async def bad(key: str, **_: object) -> KeyCheck:
        return KeyCheck(status="invalid", message="OpenRouter rejected this key")

    monkeypatch.setitem(check_key.PROVIDERS, "openrouter", ("OPENROUTER_API_KEY", bad))
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        assert check_key.main(["openrouter"]) == 1


def test_gemini_key_check_uses_a_header_and_maps_statuses() -> None:
    from app.llm.providers import check_gemini_key

    seen: list[httpx.Request] = []

    def run(status: int) -> KeyCheck:
        def handle(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(status, json={})

        return run_async(lambda: check_gemini_key(SECRET, http=client(httpx.MockTransport(handle))))

    assert run(200).status == "valid" and run(429).status == "valid"
    assert run(400).status == "invalid" and run(403).status == "invalid"
    assert run(500).status == "unavailable"
    assert seen[0].headers["x-goog-api-key"] == SECRET and SECRET not in str(seen[0].url)
