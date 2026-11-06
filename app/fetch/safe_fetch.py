"""safe_fetch: the only place that makes HTTP requests to arbitrary URLs (AGENTS.md).

Defences, in the order a request meets them (SPEC criteria 10 to 14):

 1. Only http and https, no credentials in the URL, only ports 80 and 443.
 2. Obvious internal names (localhost, *.internal, ...) are refused before DNS.
 3. DNS is resolved first. EVERY returned address must be public; one private answer blocks
    the whole host (an attacker controls their own DNS answers).
 4. The connection is pinned to the validated IP (Host header and TLS SNI carry the name), so
    DNS cannot change between the check and the connect (DNS rebinding).
 5. Redirects are followed by hand, at most 3, and steps 1 to 4 run again on every hop.
 6. robots.txt is honoured for every host contacted (fetched through the same safe path).
 7. 10s timeout per request, 1MB cap on the DECODED body (bounded decompression, so gzip bombs
    cannot blow up memory), text content types only, identifiable User-Agent.
 8. The HTTP client ignores proxy environment variables (a proxy would bypass the IP checks)
    and never stores cookies.

A page we could not read is never "brand absent": text and content_hash exist only when
fetch_status is "ok", and `FetchResult.readable` says whether absence may be asserted at all.
Fetched text is untrusted data. This module only returns it; it never interprets it.
"""

import asyncio
import hashlib
import ipaddress
import logging
import re
import socket
import time
import zlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from html.parser import HTMLParser
from typing import Literal, Protocol
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.observability import get_tracer
from app.schemas import FetchStatus

logger = logging.getLogger("app.fetch")

MAX_BODY_BYTES = 1_000_000
# Parse time is ~15 microseconds per tag, so cap markup density: ~2s of CPU in the worst case.
MAX_MARKUP_TAGS = 120_000
DEFAULT_TIMEOUT_S = 10.0
DEFAULT_MAX_REDIRECTS = 3
DEFAULT_CACHE_TTL = timedelta(hours=24)
DEFAULT_USER_AGENT = "AIVisibilityAuditBot/0.1 (AI visibility audit agent; respects robots.txt)"
ALLOWED_PORTS = {80, 443}
CACHEABLE_STATUSES: frozenset[str] = frozenset({"ok", "paywalled", "too_large"})
REDIRECT_CODES = {301, 302, 303, 307, 308}

BLOCKED_NAMES = {"localhost", "metadata", "metadata.google.internal", "instance-data"}
BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain", ".home.arpa")


class FetchReason(StrEnum):
    OK = "ok"
    CACHED = "cached"
    INVALID_URL = "invalid_url"
    BAD_SCHEME = "bad_scheme"
    CREDENTIALS_IN_URL = "credentials_in_url"
    PORT_NOT_ALLOWED = "port_not_allowed"
    BLOCKED_HOST = "blocked_host"
    BLOCKED_IP = "blocked_ip"
    DNS_FAILURE = "dns_failure"
    ROBOTS_DISALLOWED = "robots_disallowed"
    ROBOTS_UNAVAILABLE = "robots_unavailable"
    TIMEOUT = "timeout"
    CONNECT_ERROR = "connect_error"
    NETWORK_ERROR = "network_error"
    TOO_MANY_REDIRECTS = "too_many_redirects"
    REDIRECT_INVALID = "redirect_invalid"
    HTTP_DENIED = "http_denied"
    HTTP_NOT_FOUND = "http_not_found"
    HTTP_RATE_LIMITED = "http_rate_limited"
    HTTP_SERVER_ERROR = "http_server_error"
    HTTP_PAYMENT_REQUIRED = "http_payment_required"
    HTTP_OTHER = "http_other"
    UNSUPPORTED_CONTENT_TYPE = "unsupported_content_type"
    UNSUPPORTED_ENCODING = "unsupported_encoding"
    BAD_ENCODING = "bad_encoding"
    TOO_LARGE = "too_large"
    HTML_TOO_COMPLEX = "html_too_complex"
    PAYWALL_MARKER = "paywall_marker"
    JS_OR_EMPTY = "js_or_empty"
    INTERNAL_ERROR = "internal_error"


# ---------------------------------------------------------------- result and snapshot types


class FetchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str  # as requested
    final_url: str | None = None  # last URL attempted (differs from `url` after redirects)
    final_domain: str | None = None  # evidence is attributed to this domain
    redirect_chain: list[str] = Field(default_factory=list)
    fetch_status: FetchStatus
    reason: FetchReason
    detail: str | None = None
    http_status: int | None = None
    content_type: str | None = None
    text: str | None = None
    content_hash: str | None = None
    fetched_at: datetime
    snapshot_id: str | None = None
    from_cache: bool = False

    @model_validator(mode="after")
    def _text_only_when_ok(self) -> "FetchResult":
        # A page we could not read must never look like a page with no brand on it.
        if self.fetch_status != "ok" and (self.text is not None or self.content_hash is not None):
            raise ValueError("text and content_hash are only allowed when fetch_status is 'ok'")
        if self.fetch_status == "ok" and self.text is None:
            raise ValueError("fetch_status 'ok' requires text")
        return self

    @property
    def readable(self) -> bool:
        """True only when we hold the page text, i.e. when brand_on_page may be true/false.

        When False, brand_on_page must be null (SPEC criterion 14).
        """
        return self.fetch_status == "ok" and self.text is not None


class SnapshotRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    url: str  # normalised request URL (the cache key)
    final_url: str | None
    fetched_at: datetime
    status: FetchStatus
    text: str | None
    content_hash: str | None


class SnapshotStore(Protocol):
    async def latest_cacheable(self, url: str, since: datetime) -> SnapshotRecord | None: ...

    async def add(
        self,
        *,
        url: str,
        final_url: str | None,
        fetched_at: datetime,
        status: FetchStatus,
        text: str | None,
        content_hash: str | None,
    ) -> str: ...


class InMemorySnapshotStore:
    """Append-only; used in tests and as the default. See db_store.py for Postgres."""

    def __init__(self) -> None:
        self.records: list[SnapshotRecord] = []

    async def latest_cacheable(self, url: str, since: datetime) -> SnapshotRecord | None:
        matches = [
            r
            for r in self.records
            if r.url == url and r.status in CACHEABLE_STATUSES and r.fetched_at >= since
        ]
        return max(matches, key=lambda r: r.fetched_at) if matches else None

    async def add(
        self,
        *,
        url: str,
        final_url: str | None,
        fetched_at: datetime,
        status: FetchStatus,
        text: str | None,
        content_hash: str | None,
    ) -> str:
        record = SnapshotRecord(
            id=str(uuid4()),
            url=url,
            final_url=final_url,
            fetched_at=fetched_at,
            status=status,
            text=text,
            content_hash=content_hash,
        )
        self.records.append(record)
        return record.id


# ---------------------------------------------------------------- IP classification

_V6_GLOBAL_UNICAST = ipaddress.ip_network("2000::/3")
_METADATA_IPS = {
    ipaddress.ip_address(a)
    for a in (
        "169.254.169.254",  # AWS, GCP, Azure, OpenStack, DigitalOcean ...
        "169.254.170.2",  # AWS ECS task metadata
        "100.100.100.200",  # Alibaba Cloud
        "168.63.129.16",  # Azure wireserver (a public address, so it needs listing)
        "192.0.0.192",  # Oracle Cloud
        "fd00:ec2::254",  # AWS IPv6 metadata
    )
}


def is_blocked_ip(address: str) -> str | None:
    """Return why `address` must not be contacted, or None if it is a public address.

    Anything that is not a plain public unicast address is refused: private, loopback,
    link-local, carrier-grade NAT, documentation and reserved ranges, multicast, cloud metadata
    addresses, and IPv6 forms that can wrap an IPv4 address (mapped, NAT64, 6to4, Teredo).
    """
    try:
        ip = ipaddress.ip_address(address.strip().split("%", 1)[0])
    except ValueError:
        return "not a valid IP address"
    if ip in _METADATA_IPS:
        return "cloud metadata address"
    if isinstance(ip, ipaddress.IPv6Address):
        if ip not in _V6_GLOBAL_UNICAST:
            return "outside global unicast space (loopback, link-local, ULA, mapped, NAT64, ...)"
        embedded: list[ipaddress.IPv4Address] = []
        if ip.sixtofour is not None:
            embedded.append(ip.sixtofour)
        if ip.teredo is not None:
            embedded.extend(ip.teredo)
        for inner in embedded:
            why = is_blocked_ip(str(inner))
            if why:
                return f"embeds {inner}: {why}"
    if not ip.is_global:
        return "not a public address (private, loopback, link-local, reserved, ...)"
    if ip.is_multicast:
        return "multicast address"
    return None


# ---------------------------------------------------------------- URL helpers


def normalize_url(url: str) -> str:
    """Cache key: lower-case scheme and host, default port and fragment removed."""
    raw = url.strip()
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return raw
    if not parts.scheme or not parts.hostname:
        return raw
    scheme = parts.scheme.lower()
    host = parts.hostname.lower().rstrip(".")
    if ":" in host:
        host = f"[{host}]"
    default = 443 if scheme == "https" else 80
    netloc = host if port in (None, default) else f"{host}:{port}"
    return f"{scheme}://{netloc}{parts.path or '/'}" + (f"?{parts.query}" if parts.query else "")


def url_for_logs(url: str) -> str:
    """The URL without credentials, query string or fragment, which can carry secrets."""
    try:
        parts = urlsplit(url.strip())
        host = parts.hostname
        port = f":{parts.port}" if parts.port else ""
    except ValueError:  # unbalanced brackets, a non-numeric or out-of-range port
        return "<unparseable url>"
    if not host:
        return "<no host>"
    return f"{parts.scheme}://{host}{port}{parts.path}"[:300]


def _domain_of(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


# ---------------------------------------------------------------- text extraction

# Not page prose: code, templates and form controls (a <textarea> or <option> holds data).
_SKIP_TAGS = {
    "script", "style", "noscript", "template", "svg", "textarea", "select", "datalist",
}  # fmt: skip
_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
    "source", "track", "wbr",
}  # fmt: skip
_BLOCK_TAGS = {
    "p", "div", "br", "hr", "li", "ul", "ol", "dl", "dt", "dd", "h1", "h2", "h3", "h4", "h5", "h6",
    "tr", "td", "th", "table", "section", "article", "header", "footer", "nav", "aside", "main",
    "blockquote", "pre", "form", "title", "figure", "figcaption", "details", "summary", "body",
}  # fmt: skip
# Inline-style ways of hiding text from a human. (External stylesheets and classes cannot be
# evaluated here; see the security notes.)
_ROOT_TAGS = {"html", "body"}
_HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none"
    r"|visibility\s*:\s*hidden"
    r"|opacity\s*:\s*0(?:\.0+)?\s*(?:;|!|$)"
    r"|font-size\s*:\s*0(?:px|pt|em|rem|%)?\s*(?:;|!|$)"
    r"|(?:left|right|top|bottom|text-indent|margin-left|margin-right|margin-top)"
    r"\s*:\s*-\s*\d{4,}"
    r"|clip\s*:\s*rect\(\s*0",
    re.IGNORECASE,
)


class _TextExtractor(HTMLParser):
    """Visible text only: no scripts, styles, comments, attributes (alt text) or hidden nodes."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._open: list[tuple[str, bool]] = []
        self._hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _VOID_TAGS:
            if tag in _BLOCK_TAGS:
                self.parts.append(" ")
            return
        attr = {k: (v or "") for k, v in attrs}
        hidden = (
            tag in _SKIP_TAGS
            or "hidden" in attr
            or attr.get("aria-hidden", "").lower() == "true"
            # A hidden <html>/<body> is the "reveal after JavaScript runs" trick, not a hidden
            # message: everything on the page would be equally "hidden". Elements inside still are.
            or (tag not in _ROOT_TAGS and bool(_HIDDEN_STYLE.search(attr.get("style", ""))))
        )
        self._open.append((tag, hidden))
        if hidden:
            self._hidden_depth += 1
        if tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_TAGS:
            return
        for i in range(len(self._open) - 1, -1, -1):
            if self._open[i][0] == tag:
                for _, hidden in self._open[i:]:
                    if hidden:
                        self._hidden_depth -= 1
                del self._open[i:]
                break
        if tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._hidden_depth == 0:
            self.parts.append(data)


def extract_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return " ".join("".join(parser.parts).split())


_PAYWALL_JSON = re.compile(r"\"?isAccessibleForFree\"?\s*:\s*\"?false", re.IGNORECASE)
_PAYWALL_PHRASES = (
    "subscribe to continue reading",
    "subscribe to read",
    "subscribe to unlock",
    "this content is for subscribers",
    "sign in to continue reading",
    "log in to continue reading",
    "to continue reading, please",
    "you've reached your limit of free articles",
    "you have reached your limit of free articles",
    "become a member to read",
)
_PAYWALL_PHRASE_MAX_TEXT = 3000  # phrases only count on short pages; long articles are not walls
_MIN_ALNUM_CHARS = 20


def _classify_content(raw_html: str, text: str) -> tuple[FetchStatus, FetchReason] | None:
    """Heuristics for pages that are reachable but unreadable. None means readable."""
    if _PAYWALL_JSON.search(raw_html):
        return "paywalled", FetchReason.PAYWALL_MARKER
    lowered = text.lower()
    if len(text) < _PAYWALL_PHRASE_MAX_TEXT and any(p in lowered for p in _PAYWALL_PHRASES):
        return "paywalled", FetchReason.PAYWALL_MARKER
    if sum(ch.isalnum() for ch in text) < _MIN_ALNUM_CHARS:
        return "unreachable", FetchReason.JS_OR_EMPTY
    return None


# ---------------------------------------------------------------- DNS


Resolver = Callable[[str, int], Awaitable[list[str]]]


async def system_resolver(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]).split("%", 1)[0] for info in infos))


# ---------------------------------------------------------------- the fetcher


class _Stop(Exception):  # noqa: N818 - control flow, not an error condition
    def __init__(
        self,
        status: FetchStatus,
        reason: FetchReason,
        detail: str | None = None,
    ) -> None:
        super().__init__(f"{status}: {reason}")
        self.status = status
        self.reason = reason
        self.detail = detail


@dataclass
class _Target:
    url: str
    scheme: str
    host: str
    port: int
    ips: list[str]
    path_query: str

    @property
    def host_header(self) -> str:
        default = 443 if self.scheme == "https" else 80
        return self.host if self.port == default else f"{self.host}:{self.port}"

    @property
    def origin(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"


@dataclass
class _Response:
    status: int
    headers: httpx.Headers
    body: bytes | None


@dataclass
class _State:
    final_url: str | None = None
    chain: list[str] = field(default_factory=list)
    http_status: int | None = None
    content_type: str | None = None


@dataclass
class _Done:
    status: FetchStatus
    reason: FetchReason
    detail: str | None = None
    text: str | None = None


@dataclass
class _Robots:
    kind: Literal["parser", "allow_all", "disallow_all"]
    parser: RobotFileParser | None = None
    detail: str | None = None


class SafeFetcher:
    def __init__(
        self,
        *,
        resolver: Resolver = system_resolver,
        transport: httpx.AsyncBaseTransport | None = None,
        store: SnapshotStore | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        max_bytes: int = MAX_BODY_BYTES,
        cache_ttl: timedelta = DEFAULT_CACHE_TTL,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._resolver = resolver
        self._store: SnapshotStore = store or InMemorySnapshotStore()
        self.user_agent = user_agent
        self.timeout_s = timeout_s
        self.max_redirects = max_redirects
        self.max_bytes = max_bytes
        self.cache_ttl = cache_ttl
        self._now = now
        self._robots: dict[str, _Robots] = {}
        # We drive the bare transport, not an httpx client. That removes, by construction,
        # everything that could undermine the checks: automatic redirects (httpx parses every
        # Location header, even malicious ones), proxies from the environment (they would
        # bypass the IP checks), cookie jars and auth handling. `proxy` is never passed.
        self.transport: httpx.AsyncBaseTransport = transport or httpx.AsyncHTTPTransport(
            retries=0, trust_env=False
        )

    @property
    def robots_token(self) -> str:
        return self.user_agent.split("/", 1)[0].split()[0]

    async def aclose(self) -> None:
        await self.transport.aclose()

    # ------------------------------------------------------------ public entry point

    async def fetch(self, url: str) -> FetchResult:
        """Fetch `url`. Never raises for network, policy or content problems.

        Traced as a `fetch` span and logged without query strings or credentials.
        """
        shown = url_for_logs(url)
        started = time.perf_counter()
        with get_tracer().span("fetch", kind="fetch", url=shown) as span:
            result = await self._fetch_guarded(url)
            latency_ms = round((time.perf_counter() - started) * 1000)
            span.update(
                status=result.fetch_status,
                reason=str(result.reason),
                http_status=result.http_status,
                from_cache=result.from_cache,
                chars=len(result.text) if result.text is not None else 0,
                latency_ms=latency_ms,
            )
        logger.info(
            "page_fetch",
            extra={
                "url": shown,
                "status": result.fetch_status,
                "reason": str(result.reason),
                "from_cache": result.from_cache,
                "latency_ms": latency_ms,
            },
        )
        return result

    async def _fetch_guarded(self, url: str) -> FetchResult:
        raw = url.strip()
        try:
            return await self._fetch(raw)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the contract is "never raise"
            logger.warning("fetch_internal_error", extra={"error": type(exc).__name__})
            return FetchResult(
                url=raw,
                fetch_status="error",
                reason=FetchReason.INTERNAL_ERROR,
                detail=type(exc).__name__,
                fetched_at=self._now(),
            )

    async def _fetch(self, raw: str) -> FetchResult:
        key = normalize_url(raw)
        now = self._now()
        cached = await self._store.latest_cacheable(key, now - self.cache_ttl)
        if cached is not None:
            return FetchResult(
                url=raw,
                final_url=cached.final_url,
                final_domain=_domain_of(cached.final_url),
                fetch_status=cached.status,
                reason=FetchReason.OK if cached.status == "ok" else FetchReason.CACHED,
                detail=None if cached.status == "ok" else "served from cache",
                text=cached.text,
                content_hash=cached.content_hash,
                fetched_at=cached.fetched_at,
                snapshot_id=cached.id,
                from_cache=True,
            )

        state = _State()
        try:
            done = await self._run(raw, state)
        except _Stop as stop:
            done = _Done(stop.status, stop.reason, stop.detail)

        text = done.text if done.status == "ok" else None
        content_hash = hashlib.sha256(text.encode()).hexdigest() if text is not None else None
        snapshot_id = await self._store.add(
            url=key,
            final_url=state.final_url,
            fetched_at=now,
            status=done.status,
            text=text,
            content_hash=content_hash,
        )
        return FetchResult(
            url=raw,
            final_url=state.final_url,
            final_domain=_domain_of(state.final_url),
            redirect_chain=state.chain,
            fetch_status=done.status,
            reason=done.reason,
            detail=done.detail,
            http_status=state.http_status,
            content_type=state.content_type,
            text=text,
            content_hash=content_hash,
            fetched_at=now,
            snapshot_id=snapshot_id,
        )

    # ------------------------------------------------------------ redirect loop

    async def _run(self, raw: str, state: _State) -> _Done:
        current = raw
        for hop in range(self.max_redirects + 1):
            state.final_url = current
            target = await self._validate(current)
            await self._check_robots(target)

            def policy(resp: httpx.Response) -> bool:
                state.http_status = resp.status_code
                state.content_type = resp.headers.get("content-type")
                return self._page_policy(resp)

            response = await self._send(target, target.path_query, policy)
            if response.status in REDIRECT_CODES:
                location = response.headers.get("location", "").strip()
                if not location:
                    raise _Stop("error", FetchReason.REDIRECT_INVALID, "redirect without Location")
                if hop == self.max_redirects:
                    raise _Stop(
                        "error",
                        FetchReason.TOO_MANY_REDIRECTS,
                        f"more than {self.max_redirects} redirects",
                    )
                current = urljoin(current, location)
                state.chain.append(current)
                continue
            # CPU-bound parsing of untrusted markup runs in a worker thread so one heavy page
            # cannot freeze the event loop (and every other job on this worker).
            return await asyncio.to_thread(self._build_done, response)
        raise _Stop("error", FetchReason.TOO_MANY_REDIRECTS)  # pragma: no cover - loop returns

    def _page_policy(self, resp: httpx.Response) -> bool:
        """Decide from the headers alone whether to read the body. Raises _Stop to refuse."""
        code = resp.status_code
        if code in REDIRECT_CODES:
            return False
        if code == 402:
            raise _Stop("paywalled", FetchReason.HTTP_PAYMENT_REQUIRED, "HTTP 402")
        if code in (401, 403, 451):
            raise _Stop("blocked", FetchReason.HTTP_DENIED, f"HTTP {code}")
        if code in (404, 410):
            raise _Stop("unreachable", FetchReason.HTTP_NOT_FOUND, f"HTTP {code}")
        if code == 429:
            raise _Stop("unreachable", FetchReason.HTTP_RATE_LIMITED, "HTTP 429")
        if code >= 500:
            raise _Stop("unreachable", FetchReason.HTTP_SERVER_ERROR, f"HTTP {code}")
        if not 200 <= code < 300:
            raise _Stop("error", FetchReason.HTTP_OTHER, f"HTTP {code}")
        content_type = resp.headers.get("content-type", "")
        media = content_type.split(";", 1)[0].strip().lower()
        if not (media.startswith("text/") or media == "application/xhtml+xml"):
            raise _Stop(
                "error",
                FetchReason.UNSUPPORTED_CONTENT_TYPE,
                f"content type {content_type!r} is not text",
            )
        return True

    def _build_done(self, response: _Response) -> _Done:
        body = response.body or b""
        content_type = response.headers.get("content-type", "")
        media = content_type.split(";", 1)[0].strip().lower()
        decoded = _decode(body, content_type)
        if media != "text/plain" and decoded.count("<") > MAX_MARKUP_TAGS:
            # Deterministic CPU guard: parsing costs ~15 microseconds per tag.
            raise _Stop(
                "error",
                FetchReason.HTML_TOO_COMPLEX,
                f"more than {MAX_MARKUP_TAGS} tags in {len(body)} bytes",
            )
        text = " ".join(decoded.split()) if media == "text/plain" else extract_text(decoded)
        verdict = _classify_content(decoded, text)
        if verdict is not None:
            status, reason = verdict
            return _Done(status, reason, "page is not readable as text")
        return _Done("ok", FetchReason.OK, text=text)

    # ------------------------------------------------------------ validation (steps 1 to 3)

    async def _validate(self, url: str) -> _Target:
        if not url or any(ord(c) <= 0x20 or ord(c) == 0x7F for c in url):
            raise _Stop("error", FetchReason.INVALID_URL, "empty URL or whitespace/control chars")
        try:
            parts = urlsplit(url)
        except ValueError as exc:
            raise _Stop("error", FetchReason.INVALID_URL, str(exc)) from exc
        scheme = parts.scheme.lower()
        if scheme not in ("http", "https"):
            raise _Stop("blocked", FetchReason.BAD_SCHEME, f"scheme {scheme!r} is not allowed")
        if parts.username is not None or parts.password is not None or "@" in parts.netloc:
            raise _Stop("blocked", FetchReason.CREDENTIALS_IN_URL, "credentials in URL")
        try:
            port = parts.port
        except ValueError as exc:
            raise _Stop("error", FetchReason.INVALID_URL, "invalid port") from exc
        host = parts.hostname
        if not host:
            raise _Stop("error", FetchReason.INVALID_URL, "no host")
        port = port or (443 if scheme == "https" else 80)
        if port not in ALLOWED_PORTS:
            raise _Stop("blocked", FetchReason.PORT_NOT_ALLOWED, f"port {port} is not allowed")
        host = host.lower().rstrip(".")
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise _Stop("error", FetchReason.INVALID_URL, "invalid host name") from exc
        if host in BLOCKED_NAMES or host.endswith(BLOCKED_SUFFIXES):
            raise _Stop("blocked", FetchReason.BLOCKED_HOST, f"{host} is an internal name")

        try:
            ips = [str(ipaddress.ip_address(host))]
        except ValueError:
            ips = await self._resolve(host, port)
        for ip in ips:
            why = is_blocked_ip(ip)
            if why:
                raise _Stop("blocked", FetchReason.BLOCKED_IP, f"{host} -> {ip}: {why}")
        path_query = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        return _Target(url, scheme, host, port, ips, path_query)

    async def _resolve(self, host: str, port: int) -> list[str]:
        try:
            async with asyncio.timeout(self.timeout_s):
                ips = await self._resolver(host, port)
        except TimeoutError as exc:
            raise _Stop("unreachable", FetchReason.TIMEOUT, "DNS lookup timed out") from exc
        except OSError as exc:  # includes socket.gaierror
            raise _Stop("unreachable", FetchReason.DNS_FAILURE, f"cannot resolve {host}") from exc
        if not ips:
            raise _Stop("unreachable", FetchReason.DNS_FAILURE, f"no addresses for {host}")
        return ips

    # ------------------------------------------------------------ sending (steps 4 and 7)

    async def _send(
        self,
        target: _Target,
        path_query: str,
        policy: Callable[[httpx.Response], bool],
    ) -> _Response:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Host": target.host_header,
        }
        for ip in target.ips:
            literal = f"[{ip}]" if ":" in ip else ip
            pinned = f"{target.scheme}://{literal}:{target.port}{path_query}"
            try:
                async with asyncio.timeout(self.timeout_s):
                    request = httpx.Request(
                        "GET",
                        pinned,
                        headers=headers,
                        extensions={
                            "sni_hostname": target.host,
                            "timeout": {
                                "connect": self.timeout_s,
                                "read": self.timeout_s,
                                "write": self.timeout_s,
                                "pool": self.timeout_s,
                            },
                        },
                    )
                    response = await self.transport.handle_async_request(request)
                    try:
                        body = await self._read_body(response) if policy(response) else None
                        return _Response(response.status_code, response.headers, body)
                    finally:
                        await response.aclose()
            except httpx.ConnectError:
                continue  # try the next validated address
            except (httpx.TimeoutException, TimeoutError) as exc:
                raise _Stop("unreachable", FetchReason.TIMEOUT, "request timed out") from exc
            except httpx.HTTPError as exc:
                raise _Stop("unreachable", FetchReason.NETWORK_ERROR, type(exc).__name__) from exc
        raise _Stop("unreachable", FetchReason.CONNECT_ERROR, f"could not connect to {target.host}")

    async def _read_body(self, response: httpx.Response) -> bytes:
        """Read at most max_bytes of DECODED body; never trust Content-Length or the encoder."""
        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > self.max_bytes:
            raise _Stop("too_large", FetchReason.TOO_LARGE, f"Content-Length {declared}")
        encoding = response.headers.get("content-encoding", "identity").strip().lower()
        decoder: zlib._Decompress | None
        if encoding in ("", "identity"):
            decoder = None
        elif encoding in ("gzip", "x-gzip"):
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        elif encoding == "deflate":
            decoder = zlib.decompressobj(zlib.MAX_WBITS)
        else:
            raise _Stop("error", FetchReason.UNSUPPORTED_ENCODING, f"encoding {encoding!r}")

        chunks: list[bytes] = []
        total = 0
        wire = 0
        async for raw in response.aiter_raw():
            wire += len(raw)
            if wire > self.max_bytes:
                raise _Stop("too_large", FetchReason.TOO_LARGE, "body exceeds the size cap")
            if decoder is None:
                total += len(raw)
                if total > self.max_bytes:
                    raise _Stop("too_large", FetchReason.TOO_LARGE, "body exceeds the size cap")
                chunks.append(raw)
                continue
            pending = raw
            try:
                while pending:
                    out = decoder.decompress(pending, self.max_bytes - total + 1)
                    total += len(out)
                    if total > self.max_bytes:
                        raise _Stop(
                            "too_large", FetchReason.TOO_LARGE, "decoded body exceeds the cap"
                        )
                    chunks.append(out)
                    pending = decoder.unconsumed_tail
            except zlib.error as exc:
                raise _Stop("error", FetchReason.BAD_ENCODING, "corrupt compressed body") from exc
        return b"".join(chunks)

    # ------------------------------------------------------------ robots.txt (step 6)

    async def _check_robots(self, target: _Target) -> None:
        robots = self._robots.get(target.origin)
        if robots is None:
            robots = await self._load_robots(target)
            self._robots[target.origin] = robots
        if robots.kind == "allow_all":
            return
        if robots.kind == "disallow_all" or robots.parser is None:
            raise _Stop("blocked", FetchReason.ROBOTS_UNAVAILABLE, robots.detail)
        if not robots.parser.can_fetch(self.robots_token, target.url):
            raise _Stop("blocked", FetchReason.ROBOTS_DISALLOWED, "disallowed by robots.txt")

    async def _load_robots(self, target: _Target) -> _Robots:
        """RFC 9309: 4xx means no rules; 5xx, timeouts and refused fetches mean disallow all."""

        def policy(resp: httpx.Response) -> bool:
            return resp.status_code == 200

        current = target
        path = "/robots.txt"
        for _ in range(self.max_redirects + 1):
            try:
                response = await self._send(current, path, policy)
            except _Stop as stop:
                return _Robots("disallow_all", detail=f"robots.txt fetch failed: {stop.reason}")
            if response.status in REDIRECT_CODES:
                location = response.headers.get("location", "").strip()
                base = f"{current.scheme}://{current.host_header}{path}"
                try:
                    current = await self._validate(urljoin(base, location))
                except _Stop as stop:
                    return _Robots("disallow_all", detail=f"robots.txt redirect: {stop.reason}")
                path = current.path_query
                continue
            if response.status == 200:
                parser = RobotFileParser()
                parser.parse(_decode(response.body or b"", "").lstrip("﻿").splitlines())
                parser.modified()  # can_fetch() refuses until a "last checked" time is set
                return _Robots("parser", parser)
            if 400 <= response.status < 500 or 200 <= response.status < 300:
                return _Robots("allow_all")
            return _Robots("disallow_all", detail=f"robots.txt returned HTTP {response.status}")
        return _Robots("disallow_all", detail="robots.txt redirected too many times")


_CHARSET = re.compile(r"charset\s*=\s*[\"']?([\w.:-]+)", re.IGNORECASE)


def _decode(body: bytes, content_type: str) -> str:
    match = _CHARSET.search(content_type)
    charset = match.group(1) if match else "utf-8"
    try:
        decoded = body.decode(charset, errors="replace")
    except LookupError:
        decoded = body.decode("utf-8", errors="replace")
    return decoded.replace("\x00", "")  # Postgres text cannot hold NUL
