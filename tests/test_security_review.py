"""Regression tests from the skeptical security review of safe_fetch and the research step.

Each test is an attack that was actually tried. The ones that found real bugs are marked BUG.
The network is mocked; nothing here opens a socket.
"""

import asyncio
import time
import unicodedata
from typing import Any

import httpx
import pytest

from app.fetch.safe_fetch import MAX_MARKUP_TAGS, FetchReason, extract_text
from app.verify.page_check import check_brand_on_page
from app.verify.verifier import normalize
from tests.conftest import run_async
from tests.test_safe_fetch import PUBLIC_IP, Net, Page, fetch, html, make_fetcher, redirect

# ---------------------------------------------------------------- hostname and URL obfuscation


@pytest.mark.parametrize(
    "url",
    [
        "http://ⓛⓞⓒⓐⓛⓗⓞⓢⓣ/",  # circled "localhost"
        "http://ｌｏｃａｌｈｏｓｔ/",  # fullwidth "localhost"
        "http://127。0。0。1/",  # ideographic full stops
        "http://local​host/",  # zero-width space
        "http://LoCaLhOsT/",
        "http://127.0.0.1./",  # trailing dot
        "http://[::1].:80/",
        "http://0/",
        "http://0x7f.1/",
        "http://017700000001/",
        "http://127.0.0.1%2f@example.com/",
        "http://%31%32%37.0.0.1/",  # percent-encoded digits in the host
        "http://example.com%00.127.0.0.1/",
    ],
)
def test_hostname_obfuscation_never_reaches_the_network(url: str) -> None:
    net = Net(dns={"0": ["0.0.0.0"], "0x7f.1": ["127.0.0.1"], "017700000001": ["127.0.0.1"]})
    result = fetch(make_fetcher(net), url)
    assert result.fetch_status != "ok", url
    assert net.requests == [], url


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com\\@127.0.0.1/",
        "http://127.0.0.1:80\\@example.com/",
        "http://example.com:80@127.0.0.1/",
        "http://user:pw@example.com/",
    ],
)
def test_userinfo_and_backslash_tricks_are_refused_outright(url: str) -> None:
    net = Net(pages={"example.com/": html()})
    result = fetch(make_fetcher(net), url)
    assert result.fetch_status == "blocked" and result.reason == FetchReason.CREDENTIALS_IN_URL
    assert net.requests == []


@pytest.mark.parametrize(
    "url", ["http://example.com#@127.0.0.1/", "http://example.com?@127.0.0.1/"]
)
def test_fragment_and_query_cannot_smuggle_a_second_host(url: str) -> None:
    net = Net(pages={"example.com/": html()})
    fetch(make_fetcher(net), url)
    assert net.hosts_contacted() == {PUBLIC_IP}


def test_header_injection_through_the_url_is_rejected() -> None:
    net = Net()
    result = fetch(make_fetcher(net), "http://example.com/\r\nHost: 127.0.0.1\r\n\r\n")
    assert result.fetch_status == "error" and result.reason == FetchReason.INVALID_URL
    assert net.requests == []


# ---------------------------------------------------------------- redirect tricks


@pytest.mark.parametrize(
    "location",
    [
        "//127.0.0.1/",  # scheme-relative
        "//169.254.169.254/latest/meta-data/",
        "HTTP://LOCALHOST/",
        "http://[::ffff:a9fe:a9fe]/",  # mapped metadata address
        "http://2852039166/",  # decimal 169.254.169.254
        "http:127.0.0.1",  # malformed, resolves relative to the current host
        "http:///127.0.0.1/",
        "\\\\127.0.0.1\\share",
        "http://example.com:6379/",
    ],
)
def test_redirect_tricks_never_reach_a_non_public_address(location: str) -> None:
    net = Net(pages={"example.com/r": redirect(location)}, dns={"2852039166": ["169.254.169.254"]})
    fetch(make_fetcher(net), "http://example.com/r")
    assert net.hosts_contacted() <= {PUBLIC_IP}, location


# ---------------------------------------------------------------- slow responses


def test_a_slow_drip_response_is_cut_off_by_the_overall_timeout() -> None:
    async def drip() -> Any:
        for _ in range(200):
            await asyncio.sleep(0.05)
            yield b"x"

    def slow(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=drip())

    net = Net(pages={"example.com/slow": slow})
    started = time.perf_counter()
    result = fetch(make_fetcher(net, timeout_s=0.3), "http://example.com/slow")
    assert result.fetch_status == "unreachable" and result.reason == FetchReason.TIMEOUT
    assert time.perf_counter() - started < 2.0  # not the 10 seconds the drip would have taken


# ---------------------------------------------------------------- CPU exhaustion (BUG)


@pytest.mark.parametrize(
    "markup",
    ["<" * 1_000_000, "<a " * 333_000, "<p>" * 333_000, "<b>x</b>" * 125_000, '<a b="' * 166_000],
    ids=["lt-bomb", "open-tags", "deep-nesting", "tag-pairs", "attributes"],
)
def test_bug_markup_bombs_are_refused_quickly_instead_of_stalling_the_worker(markup: str) -> None:
    """BUG: 1MB of '<' took 6.8s to parse, synchronously on the event loop."""
    net = Net(pages={"example.com/bomb": html(markup)})
    started = time.perf_counter()
    result = fetch(make_fetcher(net), "http://example.com/bomb")
    assert time.perf_counter() - started < 1.5
    assert result.fetch_status == "error" and result.reason == FetchReason.HTML_TOO_COMPLEX
    assert result.text is None  # an unreadable page is never "brand absent"


def test_a_densely_tagged_but_legitimate_page_is_still_read() -> None:
    body = "<ul>" + "<li><a href='/x'>word</a></li>" * 20_000 + "</ul><p>Acme is here today.</p>"
    assert body.count("<") < MAX_MARKUP_TAGS
    net = Net(pages={"example.com/dense": html(body)})
    result = fetch(make_fetcher(net), "http://example.com/dense")
    assert result.fetch_status == "ok" and "Acme is here today." in (result.text or "")


def test_bug_parsing_does_not_freeze_the_event_loop() -> None:
    """BUG: parsing ran inline in the event loop, so one heavy page froze every other job."""
    body = "<p>w</p>" * 40_000 + "<p>done and some words to make it readable</p>"
    net = Net(pages={"example.com/heavy": html(body)})
    gaps: list[float] = []

    async def scenario() -> None:
        fetcher = make_fetcher(net)
        finished = asyncio.Event()

        async def ticker() -> None:
            last = time.perf_counter()
            while not finished.is_set():
                await asyncio.sleep(0.01)
                now = time.perf_counter()
                gaps.append(now - last)
                last = now

        async def work() -> None:
            await fetcher.fetch("http://example.com/heavy")
            finished.set()

        await asyncio.gather(ticker(), work())

    run_async(scenario)
    assert gaps, "the ticker never ran"
    assert max(gaps) < 0.5, f"event loop was blocked for {max(gaps):.2f}s"


def test_bug_a_page_full_of_brand_urls_is_not_quadratic() -> None:
    """BUG: the URL-overlap check was O(matches x spans): 0.46MB of links took 69 seconds."""
    body = "see https://acme.com/x " * 20_000
    started = time.perf_counter()
    result = check_brand_on_page(body, brand="Acme", aliases=[], competitors=[])
    assert time.perf_counter() - started < 5.0
    assert result.found is False  # every occurrence is inside a URL


def test_pathological_text_stays_linear_through_the_brand_check() -> None:
    started = time.perf_counter()
    for text in ("a." * 200_000, "Acme." * 80_000, "x.com/" * 60_000, ". " * 200_000):
        check_brand_on_page(
            text + " Acme is here.", brand="Acme", aliases=[], competitors=["Rival"]
        )
    assert time.perf_counter() - started < 12.0


# ---------------------------------------------------------------- NUL bytes (BUG)


@pytest.mark.parametrize("media", ["text/plain", "text/html"])
def test_bug_nul_bytes_are_stripped_so_the_page_can_be_stored(media: str) -> None:
    """BUG: a NUL byte made Postgres reject the snapshot and the page became an internal_error."""
    body = b"<p>Acme is a very nice tool for teams.\x00 It ships weekly releases.</p>"
    net = Net(pages={"example.com/nul": Page(200, body, {"content-type": media})})
    result = fetch(make_fetcher(net), "http://example.com/nul")
    assert result.fetch_status == "ok"
    assert result.text is not None and "\x00" not in result.text
    assert "Acme is a very nice tool" in result.text


# ---------------------------------------------------------------- hidden text (BUG)

HIDDEN_STYLES = [
    "position:absolute;left:-9999px",
    "position:absolute; top : -10000px",
    "text-indent:-9999px",
    "margin-left:-99999px",
    "font-size:0",
    "font-size:0px",
    "opacity:0",
    "opacity: 0 !important",
    "display:none",
    "visibility:hidden",
    "clip:rect(0,0,0,0)",
]
VISIBLE_STYLES = [
    "opacity:0.5",
    "opacity:1",
    "font-size:0.9em",
    "font-size:14px",
    "left:-10px",
    "margin-left:-20px",
    "text-indent:-1em",
    "color:#333",
]


@pytest.mark.parametrize("style", HIDDEN_STYLES)
def test_bug_text_hidden_with_inline_css_is_not_page_text(style: str) -> None:
    """BUG: off-screen / zero-size / transparent text was counted as visible page text."""
    text = extract_text(f'<body><p>seen words</p><span style="{style}">SECRETWORD</span></body>')
    assert "SECRETWORD" not in text and "seen words" in text


@pytest.mark.parametrize("style", VISIBLE_STYLES)
def test_ordinary_inline_styles_do_not_hide_text(style: str) -> None:
    assert "KEPTWORD" in extract_text(f'<p style="{style}">KEPTWORD</p>')


@pytest.mark.parametrize(
    "fragment",
    [
        "<textarea>SECRETWORD</textarea>",
        "<select><option>SECRETWORD</option></select>",
        "<datalist><option>SECRETWORD</option></datalist>",
    ],
)
def test_form_control_contents_are_not_page_prose(fragment: str) -> None:
    assert "SECRETWORD" not in extract_text(f"<body><p>seen words</p>{fragment}</body>")


@pytest.mark.xfail(strict=True, reason="KNOWN GAP: hiding via an external stylesheet class")
def test_known_gap_text_hidden_by_a_css_class_is_still_counted() -> None:
    assert "SECRETWORD" not in extract_text('<p>seen</p><span class="sr-only">SECRETWORD</span>')


@pytest.mark.xfail(strict=True, reason="KNOWN GAP: same-colour-as-background text")
def test_known_gap_white_on_white_text_is_still_counted() -> None:
    assert "SECRETWORD" not in extract_text('<p>seen</p><span style="color:#fff">SECRETWORD</span>')


# ---------------------------------------------------------------- normalise stays equivalent


def _reference_normalize(text: str) -> str:
    """The original per-character implementation that normalize() replaced for speed."""
    kept = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    folded = unicodedata.normalize("NFKC", kept).casefold()
    spaced = "".join(ch if ch.isalnum() else " " for ch in folded)
    return " ".join(spaced.split())


def test_fast_normalize_matches_the_reference_for_every_sampled_code_point() -> None:
    mismatches = []
    for cp in list(range(0x3400)) + list(range(0x3400, 0x110000, 7)):
        if 0xD800 <= cp <= 0xDFFF:
            continue
        ch = chr(cp)
        probe = f"a{ch}b {ch}"
        if normalize(probe) != _reference_normalize(probe):
            mismatches.append(hex(cp))
    assert mismatches == []


@pytest.mark.parametrize(
    "text",
    [
        "",
        " ",
        "HubSpot, Inc.",
        "Hub​Spot",
        "Ｈｕｂ",
        "Straße",
        "a_b-c.d",
        "İstanbul",
        "café",
        "ⅠⅡ",
        "x y z",
        "\U0001d7ce\U0001d7cf",
        "...",
    ],
)
def test_fast_normalize_matches_the_reference_on_tricky_strings(text: str) -> None:
    assert normalize(text) == _reference_normalize(text)


# ---------------------------------------------------------------- what the review could not test


def test_detail_field_can_name_internal_addresses_so_the_api_must_not_expose_it() -> None:
    """Documents a design constraint for the API layer, not a fix.

    FetchResult.detail names the resolved private address ("host -> 10.0.0.5"). Returned to API
    users it would be a blind-SSRF oracle for mapping internal DNS. Keep it for logs and the
    audit trail; expose only fetch_status (and a coarse reason) through the API.
    """
    net = Net(dns={"internal.corp.example": ["10.1.2.3"]})
    result = fetch(make_fetcher(net), "http://internal.corp.example/")
    assert result.fetch_status == "blocked"
    assert "10.1.2.3" in (result.detail or "")
