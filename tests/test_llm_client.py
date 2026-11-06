import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime

import pytest

from app.llm.client import LLMClient
from app.llm.cost import CostCapExceeded, CostTracker, ModelPricing, Pricing, UnknownModelError
from app.llm.types import (
    LLMCallFailed,
    LLMResult,
    LLMTimeoutError,
    ProviderError,
    ProviderRateLimited,
    ProviderResponse,
    ProviderTimeout,
    ProviderUnavailable,
    SearchUnavailable,
)
from app.schemas import Citation
from tests.conftest import run_async

NOW = datetime(2026, 11, 3, tzinfo=UTC)

PRICING = Pricing(
    {
        "fake-model": ModelPricing(input_per_mtok=1.0, output_per_mtok=2.0, search_per_call=0.014),
    }
)


def cite(url: str = "https://example.com/a") -> Citation:
    return Citation(
        url=url,
        domain="example.com",
        title="t",
        source="grounding_metadata",
        retrieved_at=NOW,
    )


def ok_response(**kw: object) -> ProviderResponse:
    data: dict[str, object] = {
        "text": "Acme is good.",
        "citations": [],
        "input_tokens": 1000,
        "output_tokens": 500,
        "search_calls": 0,
        "raw": {"fake": True},
    }
    data.update(kw)
    return ProviderResponse(**data)  # type: ignore[arg-type]


Outcome = ProviderResponse | Exception | Callable[[], Awaitable[ProviderResponse]]


class FakeProvider:
    """Scripted provider. Each call pops the next outcome; no network, no SDK."""

    name = "fake"

    def __init__(self, outcomes: Sequence[Outcome] | Callable[[bool], Outcome]) -> None:
        self._outcomes: list[Outcome] | Callable[[bool], Outcome] = (
            outcomes if callable(outcomes) else list(outcomes)
        )
        self.calls: list[bool] = []  # the `search` flag of every call

    async def generate(self, *, model: str, prompt: str, search: bool) -> ProviderResponse:
        self.calls.append(search)
        if callable(self._outcomes):
            outcome = self._outcomes(search)
        else:
            outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, ProviderResponse):
            return outcome
        return await outcome()


class Sleeps:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def make_client(
    provider: FakeProvider,
    *,
    cap: float = 10.0,
    sleeps: Sleeps | None = None,
    timeout_s: float = 30.0,
    max_retries: int = 3,
    clock: Callable[[], float] | None = None,
) -> tuple[LLMClient, CostTracker, Sleeps]:
    sleeps = sleeps or Sleeps()
    tracker = CostTracker(cap_usd=cap)
    client = LLMClient(
        providers={"fake": provider},
        pricing=PRICING,
        tracker=tracker,
        timeout_s=timeout_s,
        max_retries=max_retries,
        backoff_base_s=1.0,
        sleep=sleeps,
        clock=clock or (lambda: 0.0),
    )
    return client, tracker, sleeps


def generate(client: LLMClient, *, search: bool = False) -> LLMResult:
    return run_async(
        lambda: client.generate(provider="fake", model="fake-model", prompt="hi", search=search)
    )


# --- happy path ---------------------------------------------------------------


def test_returns_text_tokens_latency_and_cost() -> None:
    ticks = iter([10.0, 10.25])
    client, tracker, _ = make_client(FakeProvider([ok_response()]), clock=lambda: next(ticks))
    result = generate(client)
    assert result.text == "Acme is good."
    assert result.input_tokens == 1000
    assert result.output_tokens == 500
    assert result.search_calls == 0
    assert result.latency_ms == 250
    assert result.degraded is False
    # 1000 * $1/M + 500 * $2/M
    assert result.cost_usd == pytest.approx(0.002)
    assert tracker.spent_usd == pytest.approx(0.002)


def test_search_calls_are_priced_and_counted() -> None:
    resp = ok_response(search_calls=2, citations=[cite()])
    client, tracker, _ = make_client(FakeProvider([resp]))
    result = generate(client, search=True)
    assert result.search_calls == 2
    assert result.cost_usd == pytest.approx(0.002 + 2 * 0.014)
    assert tracker.search_calls == 2


def test_citations_come_only_from_structured_metadata_not_prose() -> None:
    resp = ok_response(
        text="See https://prose-only.example/x for details.",
        citations=[cite("https://example.com/grounded")],
    )
    client, _, _ = make_client(FakeProvider([resp]))
    result = generate(client, search=True)
    urls = [c.url for c in result.citations]
    assert urls == ["https://example.com/grounded"]
    assert all(c.source == "grounding_metadata" for c in result.citations)


def test_raw_response_is_returned_untouched() -> None:
    client, _, _ = make_client(FakeProvider([ok_response(raw={"a": [1, 2]})]))
    assert generate(client).raw_response == {"a": [1, 2]}


def test_unknown_model_fails_before_any_call() -> None:
    provider = FakeProvider([ok_response()])
    client, _, _ = make_client(provider)
    with pytest.raises(UnknownModelError):
        run_async(lambda: client.generate(provider="fake", model="nope", prompt="x", search=False))
    assert provider.calls == []


def test_unknown_provider_is_rejected() -> None:
    client, _, _ = make_client(FakeProvider([]))
    with pytest.raises(KeyError):
        run_async(
            lambda: client.generate(provider="other", model="fake-model", prompt="x", search=False)
        )


# --- retry / backoff -----------------------------------------------------------


def test_retry_then_success_with_exponential_backoff() -> None:
    provider = FakeProvider([ProviderRateLimited("429"), ProviderTimeout("slow"), ok_response()])
    client, tracker, sleeps = make_client(provider)
    result = generate(client)
    assert result.text == "Acme is good."
    assert provider.calls == [False, False, False]
    assert sleeps.delays == [1.0, 2.0]
    # Failed attempts cost nothing; only the successful call is charged.
    assert tracker.spent_usd == pytest.approx(0.002)


def test_retries_exhausted_raises_after_one_try_plus_three_retries() -> None:
    provider = FakeProvider([ProviderRateLimited("429")] * 4 + [ok_response()])
    client, tracker, sleeps = make_client(provider)
    with pytest.raises(LLMCallFailed) as exc:
        generate(client)
    assert exc.value.attempts == 4
    assert isinstance(exc.value.last_error, ProviderRateLimited)
    assert len(provider.calls) == 4
    assert sleeps.delays == [1.0, 2.0, 4.0]
    assert tracker.spent_usd == 0


def test_provider_overload_503_is_retried() -> None:
    provider = FakeProvider([ProviderUnavailable("503"), ok_response()])
    client, _, sleeps = make_client(provider)
    assert generate(client).text == "Acme is good."
    assert len(provider.calls) == 2 and sleeps.delays == [1.0]


def test_non_retryable_error_is_not_retried() -> None:
    provider = FakeProvider([ProviderError("bad request"), ok_response()])
    client, _, sleeps = make_client(provider)
    with pytest.raises(LLMCallFailed) as exc:
        generate(client)
    assert exc.value.attempts == 1
    assert len(provider.calls) == 1
    assert sleeps.delays == []


# --- timeout -------------------------------------------------------------------


def test_hung_provider_times_out_and_is_retried() -> None:
    async def hang() -> ProviderResponse:
        await asyncio.sleep(5)
        return ok_response()

    provider = FakeProvider([hang, hang])
    client, _, sleeps = make_client(provider, timeout_s=0.05, max_retries=1)
    with pytest.raises(LLMTimeoutError) as exc:
        generate(client)
    assert exc.value.attempts == 2
    assert len(provider.calls) == 2
    assert sleeps.delays == [1.0]


def test_default_timeout_is_30_seconds_and_default_retries_is_3() -> None:
    client = LLMClient(providers={}, pricing=PRICING, tracker=CostTracker(cap_usd=1))
    assert client.timeout_s == 30.0
    assert client.max_retries == 3


# --- cost cap ------------------------------------------------------------------


def test_cap_exceeded_stops_next_call_without_calling_provider() -> None:
    provider = FakeProvider([ok_response(search_calls=1), ok_response()])
    client, tracker, _ = make_client(provider, cap=0.01)
    first = generate(client, search=True)  # costs 0.016, which crosses the 0.01 cap
    assert first.search_calls == 1
    with pytest.raises(CostCapExceeded) as exc:
        generate(client)
    assert exc.value.cap_usd == 0.01
    assert exc.value.spent_usd == pytest.approx(0.016)
    assert len(provider.calls) == 1
    assert tracker.exhausted


def test_cap_counts_search_calls_not_only_tokens() -> None:
    # Tokens alone are 0.002, far below the cap; searches push it over.
    provider = FakeProvider([ok_response(search_calls=3)])
    client, tracker, _ = make_client(provider, cap=0.03)
    generate(client, search=True)
    assert tracker.spent_usd == pytest.approx(0.002 + 3 * 0.014)
    assert tracker.exhausted


def test_tracker_unit_behaviour() -> None:
    t = CostTracker(cap_usd=1.0)
    t.ensure_headroom()
    t.record(cost_usd=0.4, search_calls=1)
    assert t.remaining_usd == pytest.approx(0.6)
    t.record(cost_usd=0.6, search_calls=0)
    assert t.exhausted
    with pytest.raises(CostCapExceeded):
        t.ensure_headroom()


def test_default_cap_is_75_cents() -> None:
    assert CostTracker().cap_usd == 0.75


# --- degraded fallback ------------------------------------------------------------


def test_search_unavailable_falls_back_to_no_search_and_marks_degraded() -> None:
    def script(search: bool) -> Outcome:
        if search:
            return SearchUnavailable("tool not enabled")
        return ok_response(citations=[], search_calls=0)

    provider = FakeProvider(script)
    client, tracker, _ = make_client(provider)
    result = generate(client, search=True)
    assert provider.calls == [True, False]
    assert result.degraded is True
    assert result.search_calls == 0
    assert result.citations == []
    assert result.text == "Acme is good."
    assert tracker.search_calls == 0
    assert tracker.spent_usd == pytest.approx(0.002)


def test_no_degraded_flag_when_search_works() -> None:
    client, _, _ = make_client(FakeProvider([ok_response(search_calls=1)]))
    assert generate(client, search=True).degraded is False


def test_fallback_failure_is_reported_not_swallowed() -> None:
    def script(search: bool) -> Outcome:
        return SearchUnavailable("nope") if search else ProviderError("also down")

    client, _, _ = make_client(FakeProvider(script))
    with pytest.raises(LLMCallFailed):
        generate(client, search=True)


def test_default_pricing_knows_the_free_tier_friendly_gemini_model() -> None:
    from app.llm.cost import DEFAULT_PRICING

    p = DEFAULT_PRICING.get("gemini-2.5-flash")
    assert (p.input_per_mtok, p.output_per_mtok, p.search_per_call) == (0.30, 2.50, 0.035)


def test_default_pricing_knows_gemini_3_5_flash_lite() -> None:
    from app.llm.cost import DEFAULT_PRICING

    p = DEFAULT_PRICING.get("gemini-3.5-flash-lite")
    assert (p.input_per_mtok, p.output_per_mtok, p.search_per_call) == (0.30, 2.50, 0.014)
