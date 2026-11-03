"""Deterministic verifier. Pure Python: no LLM, no network, no I/O, no clock.

Every claim in a Mention gets an explicit Finding, so nothing is silently dropped or accepted:
  verified     the claim was checked against the raw text or grounding metadata and holds
  flagged      the claim is contradicted, or cannot be verified by code (needs a human)
  not_checked  a judgement call that code cannot decide (sentiment); never changes the verdict

Matching rules
  * Text is normalised before comparison: Unicode NFKC, case-folded, zero-width characters
    dropped, every other non-alphanumeric character treated as a space, whitespace collapsed.
  * Names (brand, aliases, competitors) match whole words only, so "Hub" is not found in
    "Hubble" and "HubSpot" is not found in "HubSpotty".
  * "Hub Spot" does not match "HubSpot"; list such spellings as aliases.
  * URLs and bare domains are removed before looking for a name, so a brand that appears only
    inside "https://acme.com/x" is not a mention. A brand whose name is itself a domain
    (e.g. "Monday.com") is kept.
"""

import re
import unicodedata
from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from app.schemas import Citation, Mention, Verdict


class Code(StrEnum):
    ANSWER_EMPTY = "answer_empty"
    BRAND_NAME_UNSEARCHABLE = "brand_name_unsearchable"
    BRAND_FOUND = "brand_found"
    BRAND_NOT_IN_TEXT = "brand_not_in_text"
    BRAND_ONLY_IN_URL = "brand_only_in_url"
    NEGATIVE_CONFIRMED = "negative_confirmed"
    NEGATIVE_BRAND_PRESENT = "negative_brand_present"
    QUOTE_MISSING = "quote_missing"
    QUOTE_EMPTY = "quote_empty"
    QUOTE_NOT_FOUND = "quote_not_found"
    QUOTE_NO_BRAND = "quote_no_brand"
    QUOTE_VERIFIED = "quote_verified"
    POSITION_NOT_CLAIMED = "position_not_claimed"
    POSITION_VERIFIED = "position_verified"
    POSITION_CONTRADICTED = "position_contradicted"
    POSITION_UNVERIFIABLE = "position_unverifiable"
    COMPETITOR_VERIFIED = "competitor_verified"
    COMPETITOR_NOT_IN_TEXT = "competitor_not_in_text"
    SENTIMENT_NOT_CHECKED = "sentiment_not_checked"
    CITATION_VERIFIED = "citation_verified"
    CITATION_UNVERIFIED = "citation_unverified"
    PROSE_URL_UNVERIFIED = "prose_url_unverified"


FindingStatus = Literal["verified", "flagged", "not_checked"]
Add = Callable[[Code, FindingStatus, str], None]


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: Code
    status: FindingStatus
    reason: str


class VerificationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Verdict
    findings: list[Finding]
    # URLs found only in the answer prose, ready to store with source="answer_text".
    prose_citations: list[Citation] = Field(default_factory=list)
    # Rank of the brand among {brand} + listed competitors found in the text, by first appearance.
    computed_position: int | None = None


# ---------------------------------------------------------------- text helpers

_SCHEME_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"']+", re.IGNORECASE)
_TLDS = "com|org|net|io|co|ai|app|dev|edu|gov|info|biz|tv|me|xyz|us|uk|de|fr|in"
_BARE_DOMAIN = re.compile(
    rf"(?<![\w@./-])[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.(?:{_TLDS})\b(?:/[^\s<>\"')\]]*)?",
    re.IGNORECASE,
)
_PROSE_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"'\])]+", re.IGNORECASE)
_TRAILING_PUNCT = ".,;:!?)"


def normalize(text: str) -> str:
    """Case-fold and reduce to lowercase alphanumeric words separated by single spaces."""
    kept = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    folded = unicodedata.normalize("NFKC", kept).casefold()
    spaced = "".join(ch if ch.isalnum() else " " for ch in folded)
    return " ".join(spaced.split())


def strip_urls(text: str, names: list[str]) -> str:
    """Replace URLs and bare domains with a space. Domains equal to a given name are kept."""
    keep = {n.casefold() for n in names}

    def bare(match: re.Match[str]) -> str:
        token = match.group(0)
        return token if token.casefold().rstrip(_TRAILING_PUNCT) in keep else " "

    return _BARE_DOMAIN.sub(bare, _SCHEME_URL.sub(" ", text))


def find_name(normalized_text: str, name: str) -> int | None:
    """Index of the first whole-word occurrence of `name` in already-normalised text."""
    needle = normalize(name)
    if not needle:
        return None
    index = f" {normalized_text} ".find(f" {needle} ")
    return None if index < 0 else index


def _contains_phrase(normalized_text: str, normalized_phrase: str) -> bool:
    return f" {normalized_phrase} " in f" {normalized_text} "


def url_key(url: str) -> str:
    """Comparison key: ignores scheme, "www.", host case, fragment and a trailing slash."""
    parts = urlsplit(url if "://" in url else f"https://{url}")
    host = (parts.hostname or "").lower().removeprefix("www.")
    path = parts.path.rstrip("/")
    return f"{host}{path}" + (f"?{parts.query}" if parts.query else "")


def extract_prose_urls(text: str) -> list[str]:
    """URLs written in the answer prose, trailing punctuation removed, de-duplicated."""
    seen: set[str] = set()
    urls: list[str] = []
    for match in _PROSE_URL.finditer(text):
        url = match.group(0).rstrip(_TRAILING_PUNCT)
        if url.lower().startswith("www."):
            url = f"https://{url}"
        key = url_key(url)
        if url and key not in seen:
            seen.add(key)
            urls.append(url)
    return urls


# ---------------------------------------------------------------- verification


def verify_mention(
    mention: Mention,
    *,
    raw_answer: str,
    brand: str,
    aliases: list[str],
    competitors: list[str],
    now: datetime,
) -> VerificationReport:
    """Check every claim in `mention` against the raw answer and its grounding metadata.

    `now` stamps citations created for prose-only URLs; it is a parameter so the function
    stays deterministic.
    """
    findings: list[Finding] = []

    def add(code: Code, status: FindingStatus, reason: str) -> None:
        findings.append(Finding(code=code, status=status, reason=reason))

    brand_names = [n for n in [brand, *aliases] if normalize(n)]
    all_names = [brand, *aliases, *competitors, *mention.competitors_seen]
    stripped_norm = normalize(strip_urls(raw_answer, all_names))
    full_norm = normalize(raw_answer)

    if not raw_answer.strip():
        add(
            Code.ANSWER_EMPTY,
            "flagged",
            "the raw answer is empty, so no claim can be verified (an empty answer is not "
            "verified absence)",
        )

    # --- brand
    brand_hit = _first_hit(stripped_norm, brand_names)
    if not brand_names:
        add(
            Code.BRAND_NAME_UNSEARCHABLE,
            "flagged",
            f"brand name {brand!r} has no letters or digits to search for",
        )
    elif mention.brand_mentioned:
        if brand_hit is not None:
            matched = brand_hit[1]
            how = "as the brand" if matched == brand else f"via alias {matched!r}"
            add(Code.BRAND_FOUND, "verified", f"brand found in the answer text {how}: {matched!r}")
        elif _first_hit(full_norm, brand_names) is not None:
            add(
                Code.BRAND_ONLY_IN_URL,
                "flagged",
                f"brand_mentioned is true but {brand!r} (or an alias) appears only inside a URL "
                "or domain, not in the prose",
            )
        else:
            add(
                Code.BRAND_NOT_IN_TEXT,
                "flagged",
                f"brand_mentioned is true but neither {brand!r} nor any alias appears in the "
                "answer",
            )
    elif brand_hit is None:
        add(
            Code.NEGATIVE_CONFIRMED,
            "verified",
            "brand_mentioned is false and neither the brand nor any alias appears in the prose",
        )
    else:
        add(
            Code.NEGATIVE_BRAND_PRESENT,
            "flagged",
            f"brand_mentioned is false but {brand_hit[1]!r} appears in the answer; code cannot "
            "tell a foil or comparison from a real mention, so a human must review",
        )

    # --- quote
    if mention.brand_mentioned:
        _check_quote(mention.quote, full_norm, brand_names, add)

    # --- position
    computed: int | None = None
    if mention.brand_mentioned and mention.position is not None:
        computed = _check_position(
            claimed=mention.position,
            brand_hit=brand_hit,
            brand_names=brand_names,
            competitors=competitors,
            stripped_norm=stripped_norm,
            add=add,
        )
    elif mention.brand_mentioned:
        add(Code.POSITION_NOT_CLAIMED, "not_checked", "no position was claimed")

    # --- competitors
    for name in mention.competitors_seen:
        if find_name(stripped_norm, name) is not None:
            add(Code.COMPETITOR_VERIFIED, "verified", f"competitor {name!r} appears in the answer")
        else:
            add(
                Code.COMPETITOR_NOT_IN_TEXT,
                "flagged",
                f"competitor {name!r} is claimed as seen but does not appear in the answer prose",
            )

    # --- sentiment: a judgement, reported but never decided here
    add(
        Code.SENTIMENT_NOT_CHECKED,
        "not_checked",
        f"sentiment {mention.sentiment!r} is a judgement and cannot be verified by code",
    )

    # --- citations
    prose_citations = _check_citations(mention, raw_answer, now, add)

    flagged = [f.reason for f in findings if f.status == "flagged"]
    if flagged:
        verdict = Verdict(status="flagged", reason="; ".join(flagged))
    else:
        verified = [f.reason for f in findings if f.status == "verified"]
        verdict = Verdict(status="verified", reason="; ".join(verified))
    return VerificationReport(
        verdict=verdict,
        findings=findings,
        prose_citations=prose_citations,
        computed_position=computed,
    )


def _first_hit(normalized_text: str, names: list[str]) -> tuple[int, str] | None:
    """Earliest occurrence of any name: (index, name as given), or None."""
    hits = [(i, n) for n in names if (i := find_name(normalized_text, n)) is not None]
    return min(hits, key=lambda h: h[0]) if hits else None


def _check_quote(
    quote: str | None,
    full_norm: str,
    brand_names: list[str],
    add: Add,
) -> None:
    if quote is None:
        add(Code.QUOTE_MISSING, "flagged", "brand_mentioned is true but no quote was provided")
        return
    norm_quote = normalize(quote)
    if not norm_quote:
        # An empty string is a substring of everything, so it must never count as a match.
        add(Code.QUOTE_EMPTY, "flagged", f"quote {quote!r} has no letters or digits")
        return
    if not _contains_phrase(full_norm, norm_quote):
        add(
            Code.QUOTE_NOT_FOUND,
            "flagged",
            f"quote {quote!r} does not appear verbatim in the raw answer "
            "(compared ignoring case, whitespace and punctuation)",
        )
        return
    if _first_hit(normalize(strip_urls(quote, brand_names)), brand_names) is None:
        where = (
            "only inside a URL in the quote"
            if _first_hit(norm_quote, brand_names) is not None
            else "at all"
        )
        add(
            Code.QUOTE_NO_BRAND,
            "flagged",
            f"quote {quote!r} is in the answer but names the brand or an alias {where}",
        )
        return
    add(Code.QUOTE_VERIFIED, "verified", f"quote {quote!r} found in the raw answer")


def _check_position(
    *,
    claimed: int,
    brand_hit: tuple[int, str] | None,
    brand_names: list[str],
    competitors: list[str],
    stripped_norm: str,
    add: Add,
) -> int | None:
    if brand_hit is None:
        add(
            Code.POSITION_UNVERIFIABLE,
            "flagged",
            f"position {claimed} cannot be checked because the brand is not in the answer prose",
        )
        return None
    brand_index = brand_hit[0]
    brand_norms = {normalize(n) for n in brand_names}
    before: list[tuple[int, str]] = []
    for name in competitors:
        if normalize(name) in brand_norms:
            continue
        index = find_name(stripped_norm, name)
        if index is not None and index < brand_index:
            before.append((index, name))
    before.sort()
    computed = 1 + len(before)
    names_before = ", ".join(n for _, n in before) or "none"
    if claimed == computed:
        add(
            Code.POSITION_VERIFIED,
            "verified",
            f"position {claimed} matches: listed competitors appearing first: {names_before}",
        )
    elif claimed < computed:
        add(
            Code.POSITION_CONTRADICTED,
            "flagged",
            f"position {claimed} is contradicted: {len(before)} listed competitor(s) appear "
            f"before the brand ({names_before}), so it is at least {computed}",
        )
    else:
        add(
            Code.POSITION_UNVERIFIABLE,
            "flagged",
            f"position {claimed} cannot be verified: only {len(before)} listed competitor(s) "
            f"appear before the brand ({names_before}); unlisted brands may account for the "
            "difference",
        )
    return computed


def _check_citations(mention: Mention, raw_answer: str, now: datetime, add: Add) -> list[Citation]:
    prose_urls = extract_prose_urls(raw_answer)
    prose_keys = {url_key(u) for u in prose_urls}
    grounded_keys: set[str] = set()
    already_flagged: set[str] = set()

    for citation in mention.citations:
        key = url_key(citation.url)
        if citation.source == "grounding_metadata":
            grounded_keys.add(key)
            note = (
                ""
                if key in prose_keys
                else " (not referenced in the answer text, which is fine: grounding metadata "
                "is the source of truth)"
            )
            add(
                Code.CITATION_VERIFIED,
                "verified",
                f"citation {citation.url} came from provider grounding metadata{note}",
            )
        else:
            already_flagged.add(key)
            add(
                Code.CITATION_UNVERIFIED,
                "flagged",
                f"unverified citation {citation.url}: it did not come from grounding metadata",
            )

    prose_citations: list[Citation] = []
    for url in prose_urls:
        key = url_key(url)
        if key in grounded_keys or key in already_flagged:
            continue
        add(
            Code.PROSE_URL_UNVERIFIED,
            "flagged",
            f"unverified citation {url}: it appears only in the answer prose, not in "
            "grounding metadata",
        )
        host = (urlsplit(url).hostname or "").lower()
        if host:
            prose_citations.append(
                Citation(url=url, domain=host, source="answer_text", retrieved_at=now)
            )
    return prose_citations
