"""The single place every model and search call goes through (AGENTS.md backend rules)."""

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable

import httpx

from app.llm.cost import DEFAULT_PRICING, CostTracker, Pricing
from app.llm.providers import GeminiProvider, OpenAIProvider
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

logger = logging.getLogger("app.llm")

DEFAULT_TIMEOUT_S = 30.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE_S = 1.0


class LLMClient:
    def __init__(
        self,
        *,
        providers: dict[str, Provider],
        pricing: Pricing = DEFAULT_PRICING,
        tracker: CostTracker,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base_s: float = DEFAULT_BACKOFF_BASE_S,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._providers = providers
        self._pricing = pricing
        self.tracker = tracker
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
        """Build from GEMINI_API_KEY / OPENAI_API_KEY. A missing key leaves that provider out."""
        providers: dict[str, Provider] = {}
        if key := os.environ.get("GEMINI_API_KEY"):
            providers["gemini"] = GeminiProvider(api_key=key, http=http)
        if key := os.environ.get("OPENAI_API_KEY"):
            providers["openai"] = OpenAIProvider(api_key=key, http=http)
        return cls(providers=providers, tracker=tracker or CostTracker(cap_usd=cost_cap_usd))

    async def generate(
        self, *, provider: str, model: str, prompt: str, search: bool = False
    ) -> LLMResult:
        impl = self._providers[provider]
        self._pricing.get(model)  # fail on an unpriced model before spending anything
        self.tracker.ensure_headroom()

        start = self._clock()
        degraded = False
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

        cost = self._pricing.cost(
            model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            search_calls=response.search_calls,
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
        """One try plus up to `max_retries` retries on timeouts and 429s (1s, 2s, 4s backoff)."""
        attempts = 0
        while True:
            attempts += 1
            try:
                async with asyncio.timeout(self.timeout_s):
                    return await impl.generate(model=model, prompt=prompt, search=search)
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
