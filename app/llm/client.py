"""The single place every model and search call goes through (AGENTS.md backend rules)."""

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from typing import Literal, TypeVar

import httpx

from app.envfile import load_env_file
from app.llm.cost import DEFAULT_PRICING, CostTracker, Pricing
from app.llm.providers import GeminiProvider, OpenAIProvider
from app.llm.tavily import SearchTool, TavilySearch, TavilySearchResult, search_prompt
from app.llm.types import (
    RETRYABLE,
    LLMCallFailed,
    LLMResult,
    LLMTimeoutError,
    Provider,
    ProviderError,
    ProviderResponse,
    ProviderTimeout,
    SearchUnavailable,
)
from app.observability import get_tracer

logger = logging.getLogger("app.llm")

DEFAULT_TIMEOUT_S = 30.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE_S = 1.0

SearchBackend = Literal["provider", "tavily"]
_T = TypeVar("_T")


class LLMClient:
    def __init__(
        self,
        *,
        providers: dict[str, Provider],
        pricing: Pricing = DEFAULT_PRICING,
        tracker: CostTracker,
        tavily: SearchTool | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base_s: float = DEFAULT_BACKOFF_BASE_S,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._providers = providers
        self._pricing = pricing
        self.tracker = tracker
        self._tavily = tavily
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self._backoff_base_s = backoff_base_s
        self._sleep = sleep
        self._clock = clock

    @classmethod
    def from_env(
        cls,
        *,
        cost_cap_usd: float,
        http: httpx.AsyncClient | None = None,
        tracker: CostTracker | None = None,
    ) -> "LLMClient":
        """Build from GEMINI_API_KEY / OPENAI_API_KEY / TAVILY_API_KEY. A missing key leaves
        that provider (or the Tavily search mode) out."""
        load_env_file()
        providers: dict[str, Provider] = {}
        if key := os.environ.get("GEMINI_API_KEY"):
            providers["gemini"] = GeminiProvider(api_key=key, http=http)
        if key := os.environ.get("OPENAI_API_KEY"):
            providers["openai"] = OpenAIProvider(api_key=key, http=http)
        tavily: SearchTool | None = None
        if key := os.environ.get("TAVILY_API_KEY"):
            tavily = TavilySearch(api_key=key, http=http)
        return cls(
            providers=providers,
            tracker=tracker or CostTracker(cap_usd=cost_cap_usd),
            tavily=tavily,
        )

    async def generate(
        self,
        *,
        provider: str,
        model: str,
        prompt: str,
        search: bool = False,
        search_backend: SearchBackend = "provider",
    ) -> LLMResult:
        """One model call, traced as a `generation` span (plus a `search` span when the provider
        searched for us). Content is only recorded if the tracer is configured to capture it."""
        tracer = get_tracer()
        with tracer.span(
            "llm",
            kind="generation",
            provider=provider,
            model=model,
            search=search,
            search_backend=search_backend,
        ) as span:
            span.content(input=prompt)
            result = await self._generate(
                provider=provider,
                model=model,
                prompt=prompt,
                search=search,
                search_backend=search_backend,
            )
            span.update(
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                cost_usd=result.cost_usd,
                latency_ms=result.latency_ms,
                search_calls=result.search_calls,
                degraded=result.degraded,
            )
            span.content(output=result.text)
            if result.search_calls:
                per_call = (
                    self._pricing.tavily_per_search
                    if search_backend == "tavily"
                    else self._pricing.get(model).search_per_call
                )
                with tracer.span(
                    "search",
                    kind="search",
                    provider="tavily" if search_backend == "tavily" else provider,
                    calls=result.search_calls,
                    cost_usd=per_call * result.search_calls,
                ):
                    pass
            return result

    async def _tavily_search(self, question: str) -> TavilySearchResult | None:
        """Run the Tavily search with the usual retries. None means search is unavailable."""
        if self._tavily is None:
            logger.warning("tavily_not_configured")
            return None
        tool = self._tavily
        try:
            return await self._retrying(lambda: tool.search(question), search=True)
        except (SearchUnavailable, LLMCallFailed) as exc:  # LLMTimeoutError is a subclass
            logger.warning("tavily_unavailable_fallback", extra={"error": type(exc).__name__})
            return None

    async def _generate(
        self,
        *,
        provider: str,
        model: str,
        prompt: str,
        search: bool,
        search_backend: SearchBackend = "provider",
    ) -> LLMResult:
        impl = self._providers[provider]
        self._pricing.get(model)  # fail on an unpriced model before spending anything
        self.tracker.ensure_headroom()

        start = self._clock()
        degraded = False
        tavily_result: TavilySearchResult | None = None
        extra_cost = 0.0
        if search and search_backend == "tavily":
            # We run the search and hand the model the results as data; the model gets no tools.
            tavily_result = await self._tavily_search(prompt)
            if tavily_result is None:
                degraded = True
            else:
                extra_cost = self._pricing.tavily_per_search
                prompt = search_prompt(prompt, tavily_result.hits)
            search = False
        try:
            response = await self._with_retries(impl, model, prompt, search)
        except SearchUnavailable:
            # Search tool unavailable: fall back to no search for this call and mark degraded.
            logger.warning(
                "search_unavailable_fallback", extra={"provider": provider, "model": model}
            )
            degraded = True
            response = await self._with_retries(impl, model, prompt, False)
        latency_ms = round((self._clock() - start) * 1000)

        cost = extra_cost + self._pricing.cost(
            model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            search_calls=response.search_calls,
        )
        if tavily_result is not None:
            response = response.model_copy(
                update={
                    "citations": tavily_result.citations,
                    "search_calls": 1,
                    "raw": {
                        "search_backend": "tavily",
                        "tavily": tavily_result.raw,
                        "model": response.raw,
                    },
                }
            )
        self.tracker.record(cost_usd=cost, search_calls=response.search_calls)
        logger.info(
            "llm_call",
            extra={
                "provider": provider,
                "model": model,
                "latency_ms": latency_ms,
                "cost_usd": cost,
                "search_calls": response.search_calls,
                "degraded": degraded,
            },
        )
        return LLMResult(
            provider=provider,
            model=model,
            text=response.text,
            citations=response.citations,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            search_calls=response.search_calls,
            latency_ms=latency_ms,
            cost_usd=cost,
            degraded=degraded,
            raw_response=response.raw,
        )

    async def _with_retries(
        self, impl: Provider, model: str, prompt: str, search: bool
    ) -> ProviderResponse:
        return await self._retrying(
            lambda: impl.generate(model=model, prompt=prompt, search=search), search=search
        )

    async def _retrying(self, call: Callable[[], Awaitable[_T]], *, search: bool) -> _T:
        """One try plus up to `max_retries` retries on timeouts, 429s and 502/503/504
        (1s, 2s, 4s backoff)."""
        attempts = 0
        while True:
            attempts += 1
            try:
                async with asyncio.timeout(self.timeout_s):
                    return await call()
            except SearchUnavailable as exc:
                if not search:  # a plain call was rejected: that is a real failure
                    raise LLMCallFailed(str(exc), attempts=attempts, last_error=exc) from exc
                raise
            except (TimeoutError, *RETRYABLE) as exc:
                error: Exception = (
                    exc
                    if isinstance(exc, ProviderError)
                    else ProviderTimeout(f"no response within {self.timeout_s}s")
                )
                if attempts > self.max_retries:
                    cls = LLMTimeoutError if isinstance(error, ProviderTimeout) else LLMCallFailed
                    raise cls(
                        f"gave up after {attempts} attempts", attempts=attempts, last_error=error
                    ) from exc
                delay = self._backoff_base_s * 2 ** (attempts - 1)
                logger.warning(
                    "llm_retry",
                    extra={"attempt": attempts, "delay_s": delay, "error": type(error).__name__},
                )
                await self._sleep(delay)
            except ProviderError as exc:
                raise LLMCallFailed(str(exc), attempts=attempts, last_error=exc) from exc
