"""Tracing (Langfuse) and error reporting (Sentry), both optional and both safe by default.

One audit is one trace. Inside it, every model call is a `generation` span, the search calls the
provider made on its behalf are `search` spans, and every page fetch is a `fetch` span. When no
Langfuse keys are configured everything is a no-op, so tracing can never break an audit.

Content (prompts, answers, page text) is NOT sent to Langfuse unless LANGFUSE_CAPTURE_CONTENT=1:
fetched pages are untrusted and answers can contain customer data.
"""

import contextvars
import logging
import os
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from app.envfile import load_env_file
from app.logging_config import redact

logger = logging.getLogger("app.observability")

SpanKind = Literal["trace", "span", "generation", "search", "fetch"]


class SpanHandle(Protocol):
    def update(self, **attrs: Any) -> None: ...

    def content(self, *, input: str | None = None, output: str | None = None) -> None: ...

    def error(self, message: str) -> None: ...


class Tracer(Protocol):
    def span(
        self, name: str, *, kind: SpanKind = "span", **attrs: Any
    ) -> AbstractContextManager[SpanHandle]: ...

    def flush(self) -> None: ...


# --- no-op -------------------------------------------------------------------------------------


class _NullHandle:
    def update(self, **attrs: Any) -> None:
        return None

    def content(self, *, input: str | None = None, output: str | None = None) -> None:
        return None

    def error(self, message: str) -> None:
        return None


class NullTracer:
    @contextmanager
    def span(self, name: str, *, kind: SpanKind = "span", **attrs: Any) -> Iterator[SpanHandle]:
        yield _NullHandle()

    def flush(self) -> None:
        return None


# --- recording (tests) ---


@dataclass
class RecordedSpan:
    name: str
    kind: SpanKind
    attrs: dict[str, Any]
    index: int
    parent: int | None
    error: str | None = None
    input: str | None = None
    output: str | None = None
    ended: bool = False
    children: list[int] = field(default_factory=list)


_stack: contextvars.ContextVar[tuple[int, ...]] = contextvars.ContextVar("span_stack", default=())


class _RecordingHandle:
    def __init__(self, span: RecordedSpan) -> None:
        self._span = span

    def update(self, **attrs: Any) -> None:
        self._span.attrs.update(attrs)

    def content(self, *, input: str | None = None, output: str | None = None) -> None:
        if input is not None:
            self._span.input = input
        if output is not None:
            self._span.output = output

    def error(self, message: str) -> None:
        self._span.error = message


class RecordingTracer:
    def __init__(self) -> None:
        self.spans: list[RecordedSpan] = []
        self.flushed = 0

    @contextmanager
    def span(self, name: str, *, kind: SpanKind = "span", **attrs: Any) -> Iterator[SpanHandle]:
        stack = _stack.get()
        record = RecordedSpan(
            name=name,
            kind=kind,
            attrs=dict(attrs),
            index=len(self.spans),
            parent=stack[-1] if stack else None,
        )
        self.spans.append(record)
        if record.parent is not None:
            self.spans[record.parent].children.append(record.index)
        token = _stack.set((*stack, record.index))
        try:
            yield _RecordingHandle(record)
        except BaseException as exc:
            record.error = record.error or type(exc).__name__
            raise
        finally:
            record.ended = True
            _stack.reset(token)

    def flush(self) -> None:
        self.flushed += 1

    def of_kind(self, kind: SpanKind) -> list[RecordedSpan]:
        return [s for s in self.spans if s.kind == kind]

    def ancestors(self, span: RecordedSpan) -> list[RecordedSpan]:
        chain: list[RecordedSpan] = []
        parent = span.parent
        while parent is not None:
            chain.append(self.spans[parent])
            parent = self.spans[parent].parent
        return chain


# --- Langfuse ---

_META_LIMIT = 200  # Langfuse drops metadata values longer than this


def _meta(attrs: Mapping[str, Any]) -> dict[str, str]:
    return {k: str(v)[:_META_LIMIT] for k, v in attrs.items() if v is not None}


class _LangfuseHandle:
    def __init__(self, observation: Any, capture_content: bool) -> None:
        self._obs = observation
        self._capture = capture_content

    def update(self, **attrs: Any) -> None:
        kwargs: dict[str, Any] = {}
        if "input_tokens" in attrs or "output_tokens" in attrs:
            kwargs["usage_details"] = {
                "input": int(attrs.pop("input_tokens", 0) or 0),
                "output": int(attrs.pop("output_tokens", 0) or 0),
            }
        if "cost_usd" in attrs:
            kwargs["cost_details"] = {"total": float(attrs.pop("cost_usd") or 0)}
        if attrs:
            kwargs["metadata"] = _meta(attrs)
        self._obs.update(**kwargs)

    def content(self, *, input: str | None = None, output: str | None = None) -> None:
        if not self._capture:
            return
        kwargs: dict[str, Any] = {}
        if input is not None:
            kwargs["input"] = redact(input)
        if output is not None:
            kwargs["output"] = redact(output)
        self._obs.update(**kwargs)

    def error(self, message: str) -> None:
        self._obs.update(level="ERROR", status_message=message[:_META_LIMIT])


class LangfuseTracer:
    def __init__(self, client: Any, *, capture_content: bool = False) -> None:
        self._client = client
        self._capture = capture_content

    @contextmanager
    def span(self, name: str, *, kind: SpanKind = "span", **attrs: Any) -> Iterator[SpanHandle]:
        from langfuse import propagate_attributes

        as_type = "generation" if kind == "generation" else "span"
        kwargs: dict[str, Any] = {
            "as_type": as_type,
            "name": name,
            "metadata": _meta({**attrs, "kind": kind}),
        }
        if as_type == "generation" and attrs.get("model"):
            kwargs["model"] = str(attrs["model"])
        with self._client.start_as_current_observation(**kwargs) as observation:
            handle = _LangfuseHandle(observation, self._capture)
            try:
                if kind == "trace" and attrs.get("audit_id"):
                    with propagate_attributes(session_id=str(attrs["audit_id"]), trace_name=name):
                        yield handle
                else:
                    yield handle
            except BaseException as exc:
                handle.error(type(exc).__name__)
                raise

    def flush(self) -> None:
        try:
            self._client.flush()
        except Exception:
            logger.warning("langfuse_flush_failed")


def build_tracer(environ: Mapping[str, str] | None = None) -> Tracer:
    if environ is None:
        load_env_file()  # the Langfuse client reads os.environ, so .env must be loaded first
    env = environ if environ is not None else os.environ
    if not (env.get("LANGFUSE_PUBLIC_KEY") and env.get("LANGFUSE_SECRET_KEY")):
        return NullTracer()
    try:
        from langfuse import get_client

        return LangfuseTracer(
            get_client(), capture_content=env.get("LANGFUSE_CAPTURE_CONTENT") == "1"
        )
    except Exception:
        logger.warning("langfuse_init_failed; tracing disabled")
        return NullTracer()


# --- the process-wide tracer ---

_current: Tracer = NullTracer()


def get_tracer() -> Tracer:
    return _current


def set_tracer(tracer: Tracer) -> None:
    global _current
    _current = tracer


@contextmanager
def use_tracer(tracer: Tracer) -> Iterator[Tracer]:
    previous = _current
    set_tracer(tracer)
    try:
        yield tracer
    finally:
        set_tracer(previous)


# --- Sentry ---

_SENSITIVE_HEADERS = {"x-api-key", "authorization", "cookie", "set-cookie", "proxy-authorization"}


def _scrub(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def scrub_event(event: dict[str, Any], hint: Any = None) -> dict[str, Any]:
    """Remove credentials and request payloads from an event before it leaves the process."""
    request = event.get("request")
    if isinstance(request, dict):
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {
                k: ("[Filtered]" if k.lower() in _SENSITIVE_HEADERS else v)
                for k, v in headers.items()
            }
        for field_name in ("cookies", "data", "query_string"):
            if field_name in request:
                request[field_name] = "[Filtered]"
    event.pop("user", None)
    return _scrub(event)  # type: ignore[no-any-return]


def init_sentry(dsn: str, *, environment: str = "development", release: str | None = None) -> bool:
    """Enable error reporting when a DSN is configured. Never sends PII or request bodies."""
    if not dsn:
        return False
    import sentry_sdk

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release,
        send_default_pii=False,
        traces_sample_rate=0.0,
        max_request_body_size="never",
        before_send=scrub_event,  # type: ignore[arg-type]
    )
    return True
