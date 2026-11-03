"""Tracing and error reporting: one trace per audit, spans for LLM, search and fetch calls."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import psycopg
import pytest

from app.fetch.safe_fetch import url_for_logs
from app.observability import (
    LangfuseTracer,
    NullTracer,
    RecordingTracer,
    build_tracer,
    get_tracer,
    init_sentry,
    scrub_event,
    use_tracer,
)
from tests.api_support import build_harness
from tests.conftest import run_async
from tests.test_llm_client import FakeProvider, cite, make_client, ok_response
from tests.test_orchestrator import QUESTIONS
from tests.test_safe_fetch import Net, html, make_fetcher

Conn = psycopg.Connection[Any]
BODY: dict[str, Any] = {
    "brand": "Acme",
    "category": "crm",
    "competitors": ["Rival"],
    "questions": QUESTIONS,
    "repeats": 2,
}


def call_llm(search: bool = False, **resp: Any) -> RecordingTracer:
    client, _, _ = make_client(FakeProvider([ok_response(**resp)]))
    with use_tracer(RecordingTracer()) as tracer:
        run_async(
            lambda: client.generate(
                provider="fake", model="fake-model", prompt="SECRET PROMPT", search=search
            )
        )
    assert isinstance(tracer, RecordingTracer)
    return tracer


# --- LLM and search spans -------------------------------------------------------------------


def test_an_llm_call_is_one_generation_span_with_usage_and_cost() -> None:
    tracer = call_llm()
    (span,) = tracer.of_kind("generation")
    assert span.attrs["model"] == "fake-model" and span.attrs["provider"] == "fake"
    assert span.attrs["input_tokens"] == 1000 and span.attrs["output_tokens"] == 500
    assert span.attrs["cost_usd"] == pytest.approx(0.002) and span.ended
    assert tracer.of_kind("search") == []


def test_provider_searches_become_search_spans_under_the_generation() -> None:
    tracer = call_llm(search=True, search_calls=2, citations=[cite()])
    (search,) = tracer.of_kind("search")
    (gen,) = tracer.of_kind("generation")
    assert search.attrs["calls"] == 2 and search.attrs["cost_usd"] == pytest.approx(0.028)
    assert tracer.ancestors(search) == [gen]


def test_prompts_and_answers_are_not_in_span_attributes_by_default() -> None:
    tracer = call_llm()
    (span,) = tracer.of_kind("generation")
    assert "SECRET PROMPT" not in str(span.attrs) and "Acme is good" not in str(span.attrs)


def test_a_failing_llm_call_marks_the_span_and_still_raises() -> None:
    client, _, _ = make_client(FakeProvider([ValueError("bad")]), max_retries=0)
    with use_tracer(RecordingTracer()) as tracer:
        with pytest.raises(Exception):  # noqa: B017 - the client wraps provider errors
            run_async(
                lambda: client.generate(
                    provider="fake", model="fake-model", prompt="p", search=False
                )
            )
    assert isinstance(tracer, RecordingTracer)
    (span,) = tracer.of_kind("generation")
    assert span.error is not None and span.ended


# --- fetch spans ----------------------------------------------------------------------------


def test_a_page_fetch_is_a_fetch_span_without_the_query_string() -> None:
    net = Net({"example.com/page?token=abc123": html()})
    fetcher = make_fetcher(net)
    with use_tracer(RecordingTracer()) as tracer:
        result = run_async(
            lambda: fetcher.fetch("https://user:pw@example.com/page?token=abc123#frag")
        )
    assert isinstance(tracer, RecordingTracer)
    (span,) = tracer.of_kind("fetch")
    assert span.attrs["url"] == "https://example.com/page"
    assert span.attrs["status"] == result.fetch_status
    assert "abc123" not in str(span.attrs) and "pw" not in str(span.attrs.get("url"))


def test_a_blocked_fetch_is_still_traced_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.fetch")
    net = Net()
    fetcher = make_fetcher(net)
    with use_tracer(RecordingTracer()) as tracer:
        run_async(lambda: fetcher.fetch("http://169.254.169.254/latest/meta-data?x=secret"))
    assert isinstance(tracer, RecordingTracer)
    (span,) = tracer.of_kind("fetch")
    assert span.attrs["status"] != "ok" and "secret" not in str(span.attrs)
    logged = [r for r in caplog.records if r.getMessage() == "page_fetch"]
    assert len(logged) == 1 and "secret" not in str(logged[0].__dict__.get("url"))


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://a.test/p?q=1#f", "https://a.test/p"),
        ("https://u:pw@a.test:8443/p", "https://a.test:8443/p"),
        ("not a url", "<no host>"),
        ("http://[::1", "<unparseable url>"),
        ("http://example.com:notaport/", "<unparseable url>"),
        ("http://example.com:99999/", "<unparseable url>"),
    ],
)
def test_url_for_logs(url: str, expected: str) -> None:
    assert url_for_logs(url) == expected


# --- one trace per audit --------------------------------------------------------------------


def test_one_audit_is_one_trace_with_a_run_span_per_step(migrated_db_url: str, db: Conn) -> None:
    async def go() -> RecordingTracer:
        tracer = RecordingTracer()
        h = build_harness(migrated_db_url)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=h.app), base_url="http://t"
        ) as c:
            with use_tracer(tracer):
                r = await c.post(
                    "/audits", json=BODY, headers={"X-API-Key": "test-key", "Idempotency-Key": "t1"}
                )
                assert r.status_code == 202
                await h.queue.drain()
        await h.close()
        return tracer

    tracer = run_async(go)
    traces = tracer.of_kind("trace")
    assert len(traces) == 1 and traces[0].parent is None
    assert traces[0].attrs["status"] == "complete" and traces[0].attrs["audit_id"]
    runs = [s for s in tracer.spans if s.name == "run"]
    assert len(runs) == len(QUESTIONS) * 2  # questions x repeats, one model, one mode
    assert all(tracer.ancestors(s)[-1] is traces[0] for s in runs)
    assert tracer.flushed >= 1


def test_tracing_never_changes_the_audit_result(migrated_db_url: str, db: Conn) -> None:
    async def run(tracer: Any, key: str) -> dict[str, Any]:
        h = build_harness(migrated_db_url)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=h.app), base_url="http://t"
        ) as c:
            with use_tracer(tracer):
                r = await c.post(
                    "/audits", json=BODY, headers={"X-API-Key": "test-key", "Idempotency-Key": key}
                )
                await h.queue.drain()
            got = (
                await c.get(f"/audits/{r.json()['id']}", headers={"X-API-Key": "test-key"})
            ).json()
        await h.close()
        return dict(got["summary"])

    plain = run_async(lambda: run(NullTracer(), "a"))
    traced = run_async(lambda: run(RecordingTracer(), "b"))
    assert plain == traced


# --- Langfuse adapter -----------------------------------------------------------------------


class FakeObservation:
    def __init__(self, kwargs: dict[str, Any]) -> None:
        self.kwargs = kwargs
        self.updates: list[dict[str, Any]] = []

    def update(self, **kw: Any) -> None:
        self.updates.append(kw)


class FakeLangfuse:
    def __init__(self, fail_flush: bool = False) -> None:
        self.observations: list[FakeObservation] = []
        self.flushes = 0
        self.fail_flush = fail_flush

    @contextmanager
    def start_as_current_observation(self, **kwargs: Any) -> Iterator[FakeObservation]:
        obs = FakeObservation(kwargs)
        self.observations.append(obs)
        yield obs

    def flush(self) -> None:
        self.flushes += 1
        if self.fail_flush:
            raise RuntimeError("network down")


def test_langfuse_generation_gets_model_usage_and_cost() -> None:
    client = FakeLangfuse()
    tracer = LangfuseTracer(client)
    with tracer.span("llm", kind="generation", model="m-1", provider="p") as span:
        span.update(input_tokens=10, output_tokens=5, cost_usd=0.5, latency_ms=7)
        span.content(input="the prompt", output="the answer")
    (obs,) = client.observations
    assert obs.kwargs["as_type"] == "generation" and obs.kwargs["model"] == "m-1"
    usage = obs.updates[0]
    assert usage["usage_details"] == {"input": 10, "output": 5}
    assert usage["cost_details"] == {"total": 0.5} and usage["metadata"] == {"latency_ms": "7"}
    assert len(obs.updates) == 1  # content is NOT sent unless capture is on


def test_langfuse_content_capture_is_opt_in_and_redacted() -> None:
    client = FakeLangfuse()
    with LangfuseTracer(client, capture_content=True).span("llm", kind="generation") as span:
        span.content(input="key ava_Zq8Lm3Xv9Tb2Nc7Rk4Wd6Yh1", output="fine")
    sent = client.observations[0].updates[0]
    assert "ava_Zq8L" not in str(sent) and sent["output"] == "fine"


def test_langfuse_marks_errors_and_reraises() -> None:
    client = FakeLangfuse()
    with pytest.raises(ValueError):
        with LangfuseTracer(client).span("run"):
            raise ValueError("x")
    assert client.observations[0].updates[-1]["level"] == "ERROR"


def test_langfuse_trace_sets_the_session_to_the_audit_id() -> None:
    client = FakeLangfuse()
    with LangfuseTracer(client).span("audit", kind="trace", audit_id="abc-123"):
        from langfuse import get_client  # noqa: F401 - proves the SDK imports in this env
    assert client.observations[0].kwargs["metadata"]["audit_id"] == "abc-123"


def test_a_flush_failure_never_raises() -> None:
    LangfuseTracer(FakeLangfuse(fail_flush=True)).flush()


def test_no_keys_means_a_null_tracer_and_keys_mean_langfuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert isinstance(build_tracer({}), NullTracer)
    assert isinstance(build_tracer({"LANGFUSE_PUBLIC_KEY": "pk"}), NullTracer)  # needs both
    monkeypatch.setattr("langfuse.get_client", lambda: FakeLangfuse())
    tracer = build_tracer({"LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk"})
    assert isinstance(tracer, LangfuseTracer)


def test_a_langfuse_init_failure_degrades_to_no_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise RuntimeError("bad config")

    monkeypatch.setattr("langfuse.get_client", boom)
    tracer = build_tracer({"LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk"})
    assert isinstance(tracer, NullTracer)


def test_the_default_tracer_is_a_no_op() -> None:
    assert isinstance(get_tracer(), NullTracer)
    with get_tracer().span("x", kind="fetch", url="u") as span:
        span.update(a=1)
        span.content(input="i")
        span.error("e")


# --- Sentry ---------------------------------------------------------------------------------


def test_scrub_event_removes_credentials_and_payloads() -> None:
    event = {
        "request": {
            "headers": {"X-API-Key": "ava_Zq8Lm3Xv9Tb2Nc7Rk4Wd6Yh1", "Accept": "json"},
            "cookies": {"s": "1"},
            "data": {"brand": "Acme"},
            "query_string": "a=1",
        },
        "user": {"ip_address": "1.2.3.4"},
        "exception": {"values": [{"value": "failed with Bearer abcdefghijklmnop1234"}]},
    }
    out = scrub_event(event)
    assert out["request"]["headers"]["X-API-Key"] == "[Filtered]"
    assert out["request"]["headers"]["Accept"] == "json"
    assert out["request"]["data"] == "[Filtered]" and out["request"]["cookies"] == "[Filtered]"
    assert "user" not in out
    assert "abcdefghijklmnop1234" not in str(out)


def test_sentry_is_off_without_a_dsn_and_configured_safely_with_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sentry_sdk

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: calls.append(kw))
    assert init_sentry("") is False and calls == []
    assert init_sentry("https://k@o1.ingest.sentry.io/1", environment="prod") is True
    (kw,) = calls
    assert kw["send_default_pii"] is False and kw["environment"] == "prod"
    assert kw["before_send"] is scrub_event and kw["max_request_body_size"] == "never"
