"""Adapter tests. httpx.MockTransport stands in for the network: no live calls, no SDKs."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.llm.providers import GeminiProvider, OpenAIProvider
from app.llm.types import (
    ProviderError,
    ProviderRateLimited,
    ProviderTimeout,
    SearchUnavailable,
)
from tests.conftest import run_async

# Shapes copied from the Gemini grounding docs (Interactions API) and OpenAI web search docs.
GEMINI_SEARCH_BODY: dict[str, Any] = {
    "id": "v1_x",
    "model": "gemini-3.8-flash",
    "object": "interaction",
    "status": "completed",
    "steps": [
        {"type": "thought", "summary": [{"type": "text", "text": "thinking"}]},
        {
            "type": "google_search_call",
            "arguments": {"queries": ["UEFA Euro 2024 winner", "Spain England final score"]},
        },
        {
            "type": "google_search_result",
            "call_id": "s1",
            "result": [{"search_suggestions": "<x>"}],
        },
        {
            "type": "model_output",
            "content": [
                {
                    "type": "text",
                    "text": "Spain won Euro 2024. See https://prose.example/only",
                    "annotations": [
                        {
                            "type": "url_citation",
                            "url": "https://www.aljazeera.com/sports/euro-2024-final",
                            "title": "aljazeera.com",
                            "start_index": 0,
                            "end_index": 20,
                        },
                        {
                            "type": "url_citation",
                            "url": "https://www.aljazeera.com/sports/euro-2024-final",
                            "title": "aljazeera.com",
                            "start_index": 21,
                            "end_index": 30,
                        },
                        {
                            "type": "url_citation",
                            "url": "https://www.uefa.com/euro2024/news/spain",
                            "title": "uefa.com",
                            "start_index": 31,
                            "end_index": 40,
                        },
                    ],
                }
            ],
        },
    ],
    "usage": {
        "total_input_tokens": 100,
        "total_output_tokens": 20,
        "total_thought_tokens": 30,
        "total_tool_use_tokens": 50,
        "total_cached_tokens": 0,
        "total_tokens": 200,
    },
}

OPENAI_SEARCH_BODY: dict[str, Any] = {
    "id": "resp_1",
    "status": "completed",
    "output": [
        {
            "type": "web_search_call",
            "id": "ws_1",
            "status": "completed",
            "action": {"type": "search", "query": "latest news"},
        },
        {
            "type": "web_search_call",
            "id": "ws_2",
            "status": "completed",
            "action": {"type": "open_page", "url": "https://x.example"},
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "output_text",
                    "text": "Acme is great. Also https://prose.example/only",
                    "annotations": [
                        {
                            "type": "url_citation",
                            "url": "https://g2.com/acme",
                            "title": "G2",
                            "start_index": 0,
                            "end_index": 5,
                        },
                        {
                            "type": "url_citation",
                            "url": "https://g2.com/acme",
                            "title": "G2",
                            "start_index": 6,
                            "end_index": 9,
                        },
                    ],
                }
            ],
        },
    ],
    "usage": {"input_tokens": 300, "output_tokens": 40, "total_tokens": 340},
}


def make_http(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[httpx.AsyncClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return httpx.AsyncClient(transport=httpx.MockTransport(wrapped)), seen


def call(provider: GeminiProvider | OpenAIProvider, model: str, search: bool) -> Any:
    return run_async(lambda: provider.generate(model=model, prompt="best tool?", search=search))


# --- Gemini --------------------------------------------------------------------


def test_gemini_search_request_and_response() -> None:
    http, seen = make_http(lambda r: httpx.Response(200, json=GEMINI_SEARCH_BODY))
    provider = GeminiProvider(api_key="g-secret", http=http)
    result = call(provider, "gemini-3.8-flash", True)

    req = seen[0]
    assert req.method == "POST"
    assert str(req.url) == "https://generativelanguage.googleapis.com/v1beta/interactions"
    assert req.headers["x-goog-api-key"] == "g-secret"
    body = json.loads(req.content)
    assert body == {
        "model": "gemini-3.8-flash",
        "input": "best tool?",
        "tools": [{"type": "google_search"}],
    }

    assert result.text.startswith("Spain won Euro 2024.")
    # Citations come from annotations only, deduplicated; the prose-only URL is ignored.
    assert [c.url for c in result.citations] == [
        "https://www.aljazeera.com/sports/euro-2024-final",
        "https://www.uefa.com/euro2024/news/spain",
    ]
    assert [c.domain for c in result.citations] == ["www.aljazeera.com", "www.uefa.com"]
    assert all(c.source == "grounding_metadata" for c in result.citations)
    # Billed per executed query: two queries -> two search calls.
    assert result.search_calls == 2
    assert result.input_tokens == 100 + 50  # prompt + tool-use tokens
    assert result.output_tokens == 20 + 30  # output + thinking tokens
    assert result.raw == GEMINI_SEARCH_BODY


def test_gemini_without_search_sends_no_tools() -> None:
    body = {
        "status": "completed",
        "steps": [{"type": "model_output", "content": [{"type": "text", "text": "Paris."}]}],
        "usage": {"total_input_tokens": 7, "total_output_tokens": 2},
    }
    http, seen = make_http(lambda r: httpx.Response(200, json=body))
    result = call(GeminiProvider(api_key="k", http=http), "gemini-3.8-flash", False)
    assert "tools" not in json.loads(seen[0].content)
    assert result.text == "Paris."
    assert result.citations == []
    assert result.search_calls == 0


def test_gemini_429_and_timeout_are_retryable_errors() -> None:
    http, _ = make_http(lambda r: httpx.Response(429, json={"error": {"message": "slow down"}}))
    with pytest.raises(ProviderRateLimited):
        call(GeminiProvider(api_key="k", http=http), "m", False)

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("t", request=request)

    http2, _ = make_http(boom)
    with pytest.raises(ProviderTimeout):
        call(GeminiProvider(api_key="k", http=http2), "m", False)


def test_gemini_search_rejected_means_search_unavailable() -> None:
    http, _ = make_http(lambda r: httpx.Response(400, json={"error": {"message": "tool"}}))
    with pytest.raises(SearchUnavailable):
        call(GeminiProvider(api_key="k", http=http), "m", True)
    # The same 400 without a search tool is a plain, non-retryable error.
    with pytest.raises(ProviderError) as exc:
        call(GeminiProvider(api_key="k", http=http), "m", False)
    assert not isinstance(exc.value, SearchUnavailable)


def test_gemini_incomplete_interaction_is_an_error() -> None:
    http, _ = make_http(lambda r: httpx.Response(200, json={"status": "failed", "steps": []}))
    with pytest.raises(ProviderError):
        call(GeminiProvider(api_key="k", http=http), "m", False)


# --- OpenAI --------------------------------------------------------------------


def test_openai_search_request_and_response() -> None:
    http, seen = make_http(lambda r: httpx.Response(200, json=OPENAI_SEARCH_BODY))
    provider = OpenAIProvider(api_key="o-secret", http=http)
    result = call(provider, "gpt-6.1-terra", True)

    req = seen[0]
    assert str(req.url) == "https://api.openai.com/v1/responses"
    assert req.headers["authorization"] == "Bearer o-secret"
    assert json.loads(req.content) == {
        "model": "gpt-6.1-terra",
        "input": "best tool?",
        "tools": [{"type": "web_search"}],
    }

    assert result.text.startswith("Acme is great.")
    assert [c.url for c in result.citations] == ["https://g2.com/acme"]
    assert result.citations[0].title == "G2"
    assert result.citations[0].source == "grounding_metadata"
    # Only `search` actions are billable; open_page is not counted.
    assert result.search_calls == 1
    assert (result.input_tokens, result.output_tokens) == (300, 40)
    assert result.raw == OPENAI_SEARCH_BODY


def test_openai_without_search_sends_no_tools() -> None:
    body = {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "Hi", "annotations": []}],
            }
        ],
        "usage": {"input_tokens": 5, "output_tokens": 1},
    }
    http, seen = make_http(lambda r: httpx.Response(200, json=body))
    result = call(OpenAIProvider(api_key="k", http=http), "gpt-6.1-terra", False)
    assert "tools" not in json.loads(seen[0].content)
    assert result.text == "Hi"
    assert result.search_calls == 0


def test_openai_errors_map_to_retryable_and_search_unavailable() -> None:
    http, _ = make_http(lambda r: httpx.Response(429, json={}))
    with pytest.raises(ProviderRateLimited):
        call(OpenAIProvider(api_key="k", http=http), "m", False)
    http2, _ = make_http(lambda r: httpx.Response(400, json={"error": {"message": "x"}}))
    with pytest.raises(SearchUnavailable):
        call(OpenAIProvider(api_key="k", http=http2), "m", True)
    http3, _ = make_http(lambda r: httpx.Response(500, json={}))
    with pytest.raises(ProviderError) as exc:
        call(OpenAIProvider(api_key="k", http=http3), "m", False)
    assert not isinstance(exc.value, ProviderRateLimited)


def test_api_keys_never_appear_in_repr_or_error_text() -> None:
    http, _ = make_http(lambda r: httpx.Response(500, text="boom"))
    for provider in (
        GeminiProvider(api_key="TOPSECRET", http=http),
        OpenAIProvider(api_key="TOPSECRET", http=http),
    ):
        assert "TOPSECRET" not in repr(provider)
        assert "TOPSECRET" not in str(provider)
        with pytest.raises(ProviderError) as exc:
            call(provider, "m", False)
        assert "TOPSECRET" not in str(exc.value)
