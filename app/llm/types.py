"""Shared types for the LLM layer: provider protocol, results and errors."""

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.schemas import Citation


class ProviderResponse(BaseModel):
    """What a provider adapter returns, already parsed from the provider's structured response."""

    model_config = ConfigDict(extra="forbid")

    text: str
    citations: list[Citation] = Field(default_factory=list)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    search_calls: int = Field(default=0, ge=0)
    raw: dict[str, Any]


class LLMResult(BaseModel):
    """What callers of LLMClient get back."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    text: str
    citations: list[Citation]
    input_tokens: int
    output_tokens: int
    search_calls: int
    latency_ms: int
    cost_usd: float
    degraded: bool = False
    raw_response: dict[str, Any]


class Provider(Protocol):
    name: str

    async def generate(self, *, model: str, prompt: str, search: bool) -> ProviderResponse: ...


class ProviderError(Exception):
    """A provider call failed. Not retried unless a subclass says so."""


class ProviderTimeout(ProviderError):
    """Retryable."""


class ProviderRateLimited(ProviderError):
    """HTTP 429. Retryable."""


class ProviderUnavailable(ProviderError):
    """HTTP 502/503/504: the provider is overloaded or briefly down. Retryable."""


class SearchUnavailable(ProviderError):
    """The provider rejected the search/grounding tool. Triggers the no-search fallback."""


RETRYABLE = (ProviderTimeout, ProviderRateLimited, ProviderUnavailable)


class LLMCallFailed(Exception):
    def __init__(self, message: str, *, attempts: int, last_error: Exception) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.last_error = last_error


class LLMTimeoutError(LLMCallFailed):
    """Every attempt timed out."""
