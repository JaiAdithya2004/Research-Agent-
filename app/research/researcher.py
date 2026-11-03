"""Source evidence: fetch each citation, decide if the brand is on the page, classify the source.

A bounded loop. It stops, and returns partial results with status "capped", when any of these is
hit: 30 network fetches per audit, 5 per domain, or the shared cost cap. A page that could not be
read is never reported as "brand absent": brand_on_page is null (SPEC criterion 14).

Fetched text is untrusted data. The brand check is deterministic code (app.verify.page_check);
the only model call is source-type classification (app.research.classifier).
"""

from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.fetch.safe_fetch import FetchReason, FetchResult
from app.llm.cost import CostCapExceeded, CostTracker
from app.research.classifier import SourceClassifier
from app.schemas import Citation, SourceEvidence
from app.verify.page_check import check_brand_on_page
from app.verify.verifier import url_key

SkipReason = Literal["fetch_cap", "domain_cap", "cost_cap", "unverified_citation"]
Classification = Literal["rules", "model", "fallback", "cost_cap", "no_text"]


class ResearchCaps(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_fetches: int = Field(default=30, ge=0)
    max_per_domain: int = Field(default=5, ge=0)


class SkippedUrl(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    domain: str
    reason: SkipReason


class ResearchedPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence: SourceEvidence
    domain: str  # evidence is attributed to the final domain after redirects
    cited_domain: str
    fetch_reason: str
    from_cache: bool
    classification: Classification
    classification_reason: str
    brand_check_reason: str


class ResearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pages: list[ResearchedPage] = Field(default_factory=list)
    skipped: list[SkippedUrl] = Field(default_factory=list)
    status: Literal["complete", "capped"] = "complete"
    caps_hit: list[str] = Field(default_factory=list)
    fetches_used: int = 0
    duplicates_merged: int = 0


class Fetcher(Protocol):
    async def fetch(self, url: str) -> FetchResult: ...


def domain_key(domain: str) -> str:
    return domain.strip().lower().rstrip(".").removeprefix("www.")


def _dedupe(citations: list[Citation]) -> tuple[list[Citation], int]:
    """Merge duplicate URLs, keeping first position and preferring the grounded citation."""
    by_key: dict[str, int] = {}
    unique: list[Citation] = []
    merged = 0
    for citation in citations:
        key = url_key(citation.url)
        if key not in by_key:
            by_key[key] = len(unique)
            unique.append(citation)
            continue
        merged += 1
        i = by_key[key]
        if unique[i].source != "grounding_metadata" and citation.source == "grounding_metadata":
            unique[i] = citation
    return unique, merged


async def research_citations(
    citations: list[Citation],
    *,
    brand: str,
    aliases: list[str],
    competitors: list[str],
    fetcher: Fetcher,
    classifier: SourceClassifier,
    tracker: CostTracker,
    caps: ResearchCaps | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ResearchResult:
    caps = caps or ResearchCaps()
    unique, merged = _dedupe(citations)
    result = ResearchResult(duplicates_merged=merged)
    per_domain: Counter[str] = Counter()
    cost_stop = False

    def skip(citation: Citation, reason: SkipReason) -> None:
        result.skipped.append(SkippedUrl(url=citation.url, domain=citation.domain, reason=reason))
        if reason != "unverified_citation" and reason not in result.caps_hit:
            result.caps_hit.append(reason)

    for citation in unique:
        if citation.source != "grounding_metadata":
            skip(citation, "unverified_citation")  # prose-only URLs are flagged, not fetched
            continue
        if cost_stop or tracker.exhausted:
            skip(citation, "cost_cap")
            continue
        if result.fetches_used >= caps.max_fetches:
            skip(citation, "fetch_cap")
            continue
        dkey = domain_key(citation.domain)
        if per_domain[dkey] >= caps.max_per_domain:
            skip(citation, "domain_cap")
            continue

        try:
            fetched = await fetcher.fetch(citation.url)
        except Exception:  # noqa: BLE001 - one bad URL must not abort the audit
            fetched = FetchResult(
                url=citation.url,
                fetch_status="error",
                reason=FetchReason.INTERNAL_ERROR,
                fetched_at=now(),
            )
        if not fetched.from_cache:
            result.fetches_used += 1
            per_domain[dkey] += 1

        page, hit_cost_cap = await _research_page(
            citation, fetched, brand, aliases, competitors, classifier
        )
        result.pages.append(page)
        cost_stop = cost_stop or hit_cost_cap

    result.status = "capped" if result.caps_hit else "complete"
    return result


async def _research_page(
    citation: Citation,
    fetched: FetchResult,
    brand: str,
    aliases: list[str],
    competitors: list[str],
    classifier: SourceClassifier,
) -> tuple[ResearchedPage, bool]:
    brand_on_page: bool | None = None
    quote: str | None = None
    check_reason = f"page not read ({fetched.fetch_status}); brand_on_page is null"
    if fetched.readable and fetched.text is not None:
        check = check_brand_on_page(
            fetched.text, brand=brand, aliases=aliases, competitors=competitors
        )
        brand_on_page = check.found
        quote = check.quote if check.found else None
        check_reason = check.reason

    domain = domain_key(fetched.final_domain or citation.domain)
    cost_capped = False
    try:
        classified = await classifier.classify(
            domain=domain,
            page_text=fetched.text if fetched.readable else None,
            brand=brand,
            aliases=aliases,
            competitors=competitors,
        )
        source_type = classified.source_type
        method: Classification = classified.method
        method_reason = classified.reason
        if method == "fallback" and not fetched.readable:
            method = "no_text"
    except CostCapExceeded:
        cost_capped = True
        source_type = "other"
        method = "cost_cap"
        method_reason = "the cost cap was reached before this page could be classified"

    evidence = SourceEvidence(
        url=citation.url,
        final_url=fetched.final_url,
        fetch_status=fetched.fetch_status,
        brand_on_page=brand_on_page,
        quote=quote,
        source_type=source_type,
        snapshot_id=fetched.snapshot_id,
    )
    page = ResearchedPage(
        evidence=evidence,
        domain=domain,
        cited_domain=domain_key(citation.domain),
        fetch_reason=str(fetched.reason),
        from_cache=fetched.from_cache,
        classification=method,
        classification_reason=method_reason,
        brand_check_reason=check_reason,
    )
    return page, cost_capped
