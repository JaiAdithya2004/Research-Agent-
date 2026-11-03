"""Deterministic "is the brand on this page" check (SPEC criterion 15).

No model is involved. The code finds the brand in the page prose, picks the sentence around it as
the quote, and then runs that quote through the SAME verifier used for answers (`verify_quote`).
A page is only reported as having the brand when that verifier accepts a verbatim quote from the
page text.

    found = True   brand in prose, and a verbatim quote was verified
    found = False  page text read, and the brand is not in the prose (URLs do not count)
    found = None   cannot tell (blank text, or the code and the verifier disagree): needs review
"""

import re
import unicodedata
from bisect import bisect_right

from pydantic import BaseModel, ConfigDict

from app.verify.verifier import (
    Code,
    drop_format_chars,
    find_name,
    normalize,
    url_spans,
    verify_quote,
)

SNIPPET_MAX = 300
_CONTEXT = 120
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


class PageBrandCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    found: bool | None
    quote: str | None = None
    matched_name: str | None = None
    reason: str


def _clean(text: str) -> str:
    """Drop zero-width/format characters and apply NFKC, as the verifier's normaliser does."""
    return unicodedata.normalize("NFKC", drop_format_chars(text))


def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Sort and merge overlapping spans, so membership tests can use binary search."""
    merged: list[tuple[int, int]] = []
    for a, b in sorted(spans):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def _without(text: str, spans: list[tuple[int, int]]) -> str:
    """`text` with every span replaced by a space (spans must be merged and sorted)."""
    pieces: list[str] = []
    position = 0
    for a, b in spans:
        pieces.append(text[position:a])
        position = b
    pieces.append(text[position:])
    return " ".join(pieces)


def _inside(spans: list[tuple[int, int]], starts: list[int], start: int, end: int) -> bool:
    """Does [start, end) overlap any merged span? O(log n)."""
    i = bisect_right(starts, end - 1) - 1
    return i >= 0 and spans[i][1] > start


def _name_pattern(name: str) -> re.Pattern[str] | None:
    tokens = normalize(name).split()
    if not tokens:
        return None
    body = r"[\W_]+".join(re.escape(t) for t in tokens)
    return re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)


def _snippet(text: str, start: int, end: int) -> str:
    """The sentence around text[start:end], trimmed to a window if it is very long."""
    before = [m.end() for m in _SENTENCE_END.finditer(text, 0, start)]
    lo = before[-1] if before else 0
    after = _SENTENCE_END.search(text, end)
    hi = after.end() if after else len(text)
    if hi - lo > SNIPPET_MAX:
        lo = max(lo, start - _CONTEXT)
        hi = min(hi, end + _CONTEXT)
        if lo > 0 and text[lo - 1] != " " and text[lo] != " ":
            nxt = text.find(" ", lo)
            lo = nxt + 1 if 0 <= nxt < start else lo
        if hi < len(text) and text[hi - 1] != " " and text[hi] != " ":
            prv = text.rfind(" ", end, hi)
            hi = prv if prv > end else hi
    return text[lo:hi].strip()


def check_brand_on_page(
    page_text: str,
    *,
    brand: str,
    aliases: list[str],
    competitors: list[str] | None = None,
) -> PageBrandCheck:
    names = [n for n in [brand, *aliases] if normalize(n)]
    if not names:
        return PageBrandCheck(found=None, reason="brand name has no searchable characters")
    if not normalize(page_text):
        return PageBrandCheck(found=None, reason="page text is empty, so absence cannot be shown")

    all_names = [*names, *(competitors or [])]
    clean = _clean(page_text)
    spans = _merge(url_spans(clean, all_names))
    starts = [a for a, _ in spans]
    prose_norm = normalize(_without(clean, spans))
    # find_name returns an index, and 0 is a valid index: compare to None, never test truthiness.
    in_prose = any(find_name(prose_norm, n) is not None for n in names)
    full_norm = normalize(clean)
    anywhere = any(find_name(full_norm, n) is not None for n in names)

    candidates: list[tuple[int, int]] = []
    for name in names:
        pattern = _name_pattern(name)
        if pattern is None:
            continue
        for match in pattern.finditer(clean):
            if not _inside(spans, starts, match.start(), match.end()):
                candidates.append(match.span())
                break
    candidates.sort()

    if not in_prose and not candidates:
        reason = (
            "the brand appears only inside URLs or domains, not in the page prose"
            if anywhere
            else "the brand does not appear in the page text"
        )
        return PageBrandCheck(found=False, reason=reason)
    if not in_prose or not candidates:
        return PageBrandCheck(
            found=None,
            reason="brand matching was inconsistent on this page; needs review",
        )

    start, end = candidates[0]
    quote = _snippet(clean, start, end)
    finding = verify_quote(quote, page_text, names)
    if finding.code != Code.QUOTE_VERIFIED:
        return PageBrandCheck(
            found=None,
            reason="a quote could not be verified against the page text; needs review",
        )
    return PageBrandCheck(
        found=True,
        quote=quote,
        matched_name=clean[start:end],
        reason=f"brand found in the page text as {clean[start:end]!r} with a verbatim quote",
    )
