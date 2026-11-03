"""Provider adapters (Gemini Interactions API, OpenAI Responses API) over plain httpx.

Only app/llm/client.py should use these. Citations are read from the providers' structured
response metadata (annotations), never parsed out of the answer text.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.llm.types import (
    ProviderError,
    ProviderRateLimited,
    ProviderResponse,
    ProviderTimeout,
    SearchUnavailable,
)
from app.schemas import Citation

# HTTP statuses that, on a request with the search tool enabled, mean "tool rejected/unavailable".
_SEARCH_REJECTED = {400, 403, 404, 422, 501}


def _utcnow() -> datetime:
    return datetime.now(UTC)


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


def _citation(url: str | None, title: str | None, now: datetime) -> Citation | None:
    host = urlparse(url).hostname if url else None
    if not url or not host:
        return None
    return Citation(
        url=url,
        domain=host.lower(),
        title=title or None,
        source="grounding_metadata",
        retrieved_at=now,
    )


def _dedupe(citations: list[Citation]) -> list[Citation]:
    seen: set[str] = set()
    out: list[Citation] = []
    for c in citations:
        if c.url not in seen:
            seen.add(c.url)
            out.append(c)
    return out


class _HttpProvider:
    name: str

    def __init__(
        self,
        *,
        api_key: str,
        http: httpx.AsyncClient | None = None,
        timeout_s: float = 30.0,
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._api_key = api_key
        self._http = http or httpx.AsyncClient(timeout=timeout_s)
        self._now = now

    def __repr__(self) -> str:  # never include the key
        return f"{type(self).__name__}()"

    async def _post(
        self, url: str, headers: dict[str, str], body: dict[str, Any], *, search: bool
    ) -> dict[str, Any]:
        try:
            resp = await self._http.post(url, headers=headers, json=body)
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(f"{self.name} request timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name} transport error: {type(exc).__name__}") from exc
        if resp.status_code == 429:
            raise ProviderRateLimited(f"{self.name} rate limited (HTTP 429)")
        if search and resp.status_code in _SEARCH_REJECTED:
            raise SearchUnavailable(
                f"{self.name} rejected the search tool (HTTP {resp.status_code})"
            )
        if resp.status_code >= 400:
            raise ProviderError(f"{self.name} HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise ProviderError(f"{self.name} returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise ProviderError(f"{self.name} returned an unexpected JSON shape")
        return data


# --- Gemini: POST /v1beta/interactions, tools=[{"type": "google_search"}] ---------------


class _GAnnotation(_Lenient):
    type: str
    url: str | None = None
    title: str | None = None


class _GContent(_Lenient):
    type: str
    text: str | None = None
    annotations: list[_GAnnotation] = Field(default_factory=list)


class _GSearchArgs(_Lenient):
    queries: list[str] = Field(default_factory=list)


class _GStep(_Lenient):
    type: str
    arguments: Any = None
    content: list[_GContent] = Field(default_factory=list)


class _GUsage(_Lenient):
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_thought_tokens: int = 0
    total_tool_use_tokens: int = 0


class _GInteraction(_Lenient):
    status: str
    steps: list[_GStep] = Field(default_factory=list)
    usage: _GUsage = Field(default_factory=_GUsage)


class GeminiProvider(_HttpProvider):
    name = "gemini"
    url = "https://generativelanguage.googleapis.com/v1beta/interactions"

    async def generate(self, *, model: str, prompt: str, search: bool) -> ProviderResponse:
        body: dict[str, Any] = {"model": model, "input": prompt}
        if search:
            body["tools"] = [{"type": "google_search"}]
        data = await self._post(self.url, {"x-goog-api-key": self._api_key}, body, search=search)
        try:
            interaction = _GInteraction.model_validate(data)
        except ValidationError as exc:
            raise ProviderError("gemini response did not match the expected shape") from exc
        if interaction.status != "completed":
            raise ProviderError(f"gemini interaction ended with status {interaction.status!r}")

        now = self._now()
        texts: list[str] = []
        citations: list[Citation] = []
        search_calls = 0
        for step in interaction.steps:
            if step.type == "google_search_call":
                try:
                    args = _GSearchArgs.model_validate(step.arguments or {})
                except ValidationError as exc:
                    raise ProviderError(
                        "gemini google_search_call had an unexpected shape"
                    ) from exc
                # Billed per executed query; empty queries are ignored.
                search_calls += sum(1 for q in args.queries if q.strip())
            elif step.type == "model_output":
                for block in step.content:
                    if block.type != "text" or block.text is None:
                        continue
                    texts.append(block.text)
                    for ann in block.annotations:
                        if ann.type == "url_citation" and (c := _citation(ann.url, ann.title, now)):
                            citations.append(c)

        usage = interaction.usage
        return ProviderResponse(
            text="".join(texts),
            citations=_dedupe(citations),
            # Tool-use tokens (search results fed to the model) are billed as input, and
            # thinking tokens as output: a conservative reading of the pricing page.
            input_tokens=usage.total_input_tokens + usage.total_tool_use_tokens,
            output_tokens=usage.total_output_tokens + usage.total_thought_tokens,
            search_calls=search_calls,
            raw=data,
        )


# --- OpenAI: POST /v1/responses, tools=[{"type": "web_search"}] --------------------------


class _OAnnotation(_Lenient):
    type: str
    url: str | None = None
    title: str | None = None


class _OContent(_Lenient):
    type: str
    text: str | None = None
    annotations: list[_OAnnotation] = Field(default_factory=list)


class _OAction(_Lenient):
    type: str | None = None


class _OItem(_Lenient):
    type: str
    action: _OAction | None = None
    content: list[_OContent] = Field(default_factory=list)


class _OUsage(_Lenient):
    input_tokens: int = 0
    output_tokens: int = 0


class _OResponse(_Lenient):
    status: str | None = None
    output: list[_OItem] = Field(default_factory=list)
    usage: _OUsage = Field(default_factory=_OUsage)


class OpenAIProvider(_HttpProvider):
    name = "openai"
    url = "https://api.openai.com/v1/responses"

    async def generate(self, *, model: str, prompt: str, search: bool) -> ProviderResponse:
        body: dict[str, Any] = {"model": model, "input": prompt}
        if search:
            body["tools"] = [{"type": "web_search"}]
        data = await self._post(
            self.url, {"Authorization": f"Bearer {self._api_key}"}, body, search=search
        )
        try:
            response = _OResponse.model_validate(data)
        except ValidationError as exc:
            raise ProviderError("openai response did not match the expected shape") from exc
        if response.status not in (None, "completed"):
            raise ProviderError(f"openai response ended with status {response.status!r}")

        now = self._now()
        texts: list[str] = []
        citations: list[Citation] = []
        search_calls = 0
        for item in response.output:
            if item.type == "web_search_call":
                # Only `search` actions are billed; open_page / find_in_page are not.
                if item.action is not None and item.action.type == "search":
                    search_calls += 1
            elif item.type == "message":
                for block in item.content:
                    if block.type != "output_text" or block.text is None:
                        continue
                    texts.append(block.text)
                    for ann in block.annotations:
                        if ann.type == "url_citation" and (c := _citation(ann.url, ann.title, now)):
                            citations.append(c)

        return ProviderResponse(
            text="".join(texts),
            citations=_dedupe(citations),
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            search_calls=search_calls,
            raw=data,
        )
