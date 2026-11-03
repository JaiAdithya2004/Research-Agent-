"""The bounded research loop: caps, partial results, deterministic brand checks."""

from typing import Any

import pytest

from app.fetch.safe_fetch import FetchReason, FetchResult
from app.llm.cost import CostCapExceeded, CostTracker
from app.llm.types import LLMResult
from app.research.classifier import SourceClassifier
from app.research.researcher import ResearchCaps, ResearchResult, research_citations
from app.schemas import Citation
from tests.conftest import run_async
from tests.test_extractor import llm_result
from tests.test_safe_fetch import T0

BRAND, COMPETITORS = "Acme", ["Rival"]
ALIASES: list[str] = []
NEUTRAL = "A neutral page about project tools and the teams that use them every day."


def cite(url: str, source: str = "grounding_metadata") -> Citation:
    host = url.split("//", 1)[-1].split("/", 1)[0]
    return Citation(url=url, domain=host, title=None, source=source, retrieved_at=T0)  # type: ignore[arg-type]


def fr(url: str, status: str = "ok", text: str | None = NEUTRAL, **kw: Any) -> FetchResult:
    if status != "ok":
        text = None
    return FetchResult(
        url=url,
        final_url=kw.pop("final_url", url),
        final_domain=kw.pop("final_domain", url.split("//", 1)[-1].split("/", 1)[0]),
        fetch_status=status,  # type: ignore[arg-type]
        reason=FetchReason.OK if status == "ok" else FetchReason.HTTP_NOT_FOUND,
        text=text,
        content_hash="h" if text else None,
        fetched_at=T0,
        snapshot_id=f"snap-{url}",
        **kw,
    )


class FakeFetcher:
    def __init__(self, results: dict[str, FetchResult] | None = None, **defaults: Any) -> None:
        self.results = results or {}
        self.defaults = defaults
        self.calls: list[str] = []

    async def fetch(self, url: str) -> FetchResult:
        self.calls.append(url)
        if url in self.results:
            return self.results[url]
        return fr(url, **self.defaults)


class CostingLLM:
    """Mimics LLMClient's cost behaviour so the shared tracker really fills up."""

    def __init__(
        self, tracker: CostTracker, cost: float = 0.0, reply: str = '{"source_type": "other"}'
    ) -> None:
        self.tracker, self.cost, self.reply = tracker, cost, reply
        self.calls = 0

    async def generate(
        self, *, provider: str, model: str, prompt: str, search: bool = False
    ) -> LLMResult:
        self.tracker.ensure_headroom()
        self.calls += 1
        self.tracker.record(cost_usd=self.cost, search_calls=0)
        return llm_result(self.reply, cost=self.cost)


def run(
    citations: list[Citation],
    fetcher: Any,
    *,
    tracker: CostTracker | None = None,
    llm: Any = None,
    caps: ResearchCaps | None = None,
    brand_aliases: list[str] | None = None,
) -> ResearchResult:
    tracker = tracker or CostTracker(cap_usd=100)
    llm = llm if llm is not None else CostingLLM(tracker)
    return run_async(
        lambda: research_citations(
            citations,
            brand=BRAND,
            aliases=brand_aliases or ALIASES,
            competitors=COMPETITORS,
            fetcher=fetcher,
            classifier=SourceClassifier(llm, provider="fake", model="m"),
            tracker=tracker,
            caps=caps or ResearchCaps(),
        )
    )


# --- defaults and the happy path ----------------------------------------------------------------


def test_default_caps_match_the_spec() -> None:
    caps = ResearchCaps()
    assert (caps.max_fetches, caps.max_per_domain) == (30, 5)


def test_every_citation_is_fetched_and_researched_in_order() -> None:
    urls = [f"https://site{i}.example/p" for i in range(3)]
    fetcher = FakeFetcher()
    result = run([cite(u) for u in urls], fetcher)
    assert fetcher.calls == urls
    assert [p.evidence.url for p in result.pages] == urls
    assert result.status == "complete"
    assert result.caps_hit == [] and result.skipped == []
    assert result.fetches_used == 3


def test_brand_found_gives_true_and_a_verified_verbatim_quote() -> None:
    text = "Rival is cheap. Acme is a great tool for teams. Globex is large."
    result = run(
        [cite("https://a.example/p")],
        FakeFetcher({"https://a.example/p": fr("https://a.example/p", text=text)}),
    )
    (p,) = result.pages
    assert p.evidence.brand_on_page is True
    assert p.evidence.quote == "Acme is a great tool for teams."
    assert p.evidence.quote in text


def test_brand_missing_from_a_readable_page_is_false_not_none() -> None:
    (p,) = run([cite("https://a.example/p")], FakeFetcher()).pages
    assert p.evidence.fetch_status == "ok"
    assert p.evidence.brand_on_page is False
    assert p.evidence.quote is None


@pytest.mark.parametrize("status", ["blocked", "unreachable", "paywalled", "too_large", "error"])
def test_unfetched_pages_are_null_never_absent(status: str) -> None:
    llm_tracker = CostTracker(cap_usd=100)
    llm = CostingLLM(llm_tracker)
    (p,) = run(
        [cite("https://a.example/p")], FakeFetcher(status=status), tracker=llm_tracker, llm=llm
    ).pages
    assert p.evidence.fetch_status == status
    assert p.evidence.brand_on_page is None
    assert p.evidence.quote is None
    assert llm.calls == 0  # nothing to classify
    assert p.classification == "no_text"


def test_alias_matches_count() -> None:
    text = "AcmeHQ ships weekly releases for its customers every single month."
    (p,) = run(
        [cite("https://a.example/p")],
        FakeFetcher({"https://a.example/p": fr("https://a.example/p", text=text)}),
        brand_aliases=["AcmeHQ"],
    ).pages
    assert p.evidence.brand_on_page is True


def test_redirect_is_recorded_and_evidence_is_attributed_to_the_final_domain() -> None:
    r = fr(
        "https://short.example/x",
        final_url="https://long.example/real",
        final_domain="long.example",
    )
    (p,) = run([cite("https://short.example/x")], FakeFetcher({"https://short.example/x": r})).pages
    assert p.evidence.url == "https://short.example/x"
    assert p.evidence.final_url == "https://long.example/real"
    assert p.domain == "long.example"
    assert p.evidence.snapshot_id == "snap-https://short.example/x"


# --- source type ------------------------------------------------------------------------------------


def test_rules_decide_obvious_domains_without_spending_on_the_model() -> None:
    tracker = CostTracker(cap_usd=100)
    llm = CostingLLM(tracker, cost=0.1)
    result = run(
        [cite("https://g2.com/acme"), cite("https://www.acme.com/"), cite("https://rival.com/")],
        FakeFetcher(),
        tracker=tracker,
        llm=llm,
    )
    assert [p.evidence.source_type for p in result.pages] == ["review", "own_site", "competitor"]
    assert {p.classification for p in result.pages} == {"rules"}
    assert llm.calls == 0 and tracker.spent_usd == 0


def test_unknown_domains_use_the_model() -> None:
    tracker = CostTracker(cap_usd=100)
    llm = CostingLLM(tracker, reply='{"source_type": "news"}')
    (p,) = run([cite("https://unknown.example/p")], FakeFetcher(), tracker=tracker, llm=llm).pages
    assert p.evidence.source_type == "news" and p.classification == "model"


# --- caps --------------------------------------------------------------------------------------------


def test_at_most_30_fetches_then_partial_results() -> None:
    citations = [cite(f"https://d{i}.example/p") for i in range(40)]
    fetcher = FakeFetcher()
    result = run(citations, fetcher)
    assert len(fetcher.calls) == 30 and result.fetches_used == 30
    assert len(result.pages) == 30
    assert len(result.skipped) == 10
    assert {s.reason for s in result.skipped} == {"fetch_cap"}
    assert result.status == "capped"
    assert result.caps_hit == ["fetch_cap"]
    assert [p.evidence.url for p in result.pages] == [c.url for c in citations[:30]]


def test_at_most_5_fetches_per_domain_but_other_domains_continue() -> None:
    same = [cite(f"https://busy.example/p{i}") for i in range(8)]
    other = [cite("https://calm.example/a"), cite("https://calm.example/b")]
    fetcher = FakeFetcher()
    result = run(same + other, fetcher)
    assert sum("busy.example" in c for c in fetcher.calls) == 5
    assert sum("calm.example" in c for c in fetcher.calls) == 2
    skipped = [s for s in result.skipped if s.reason == "domain_cap"]
    assert len(skipped) == 3 and {s.domain for s in skipped} == {"busy.example"}
    assert result.status == "capped" and result.caps_hit == ["domain_cap"]


def test_domain_counting_ignores_case_and_www() -> None:
    cs = [
        cite(f"https://{h}/p{i}")
        for i, h in enumerate(
            [
                "Busy.example",
                "www.busy.example",
                "BUSY.EXAMPLE",
                "busy.example",
                "www.BUSY.example",
                "busy.example",
            ]
        )
    ]
    fetcher = FakeFetcher()
    result = run(cs, fetcher)
    assert len(fetcher.calls) == 5 and len(result.skipped) == 1


def test_custom_caps_are_respected() -> None:
    fetcher = FakeFetcher()
    result = run(
        [cite(f"https://d{i}.example/") for i in range(5)],
        fetcher,
        caps=ResearchCaps(max_fetches=2, max_per_domain=1),
    )
    assert len(fetcher.calls) == 2 and result.status == "capped"


def test_cache_hits_do_not_use_up_the_fetch_budget() -> None:
    fetcher = FakeFetcher(from_cache=True)
    result = run([cite(f"https://d{i}.example/p") for i in range(40)], fetcher)
    assert len(result.pages) == 40 and result.fetches_used == 0
    assert result.status == "complete" and result.skipped == []


def test_both_caps_hit_are_both_reported() -> None:
    cs = [cite(f"https://busy.example/p{i}") for i in range(7)] + [
        cite(f"https://d{i}.example/") for i in range(30)
    ]
    result = run(cs, FakeFetcher())
    assert set(result.caps_hit) == {"domain_cap", "fetch_cap"}
    assert result.status == "capped"


def test_cost_cap_stops_the_loop_and_returns_what_was_done() -> None:
    tracker = CostTracker(cap_usd=0.75)
    llm = CostingLLM(tracker, cost=0.30)
    cs = [cite(f"https://d{i}.example/p") for i in range(10)]
    fetcher = FakeFetcher()
    result = run(cs, fetcher, tracker=tracker, llm=llm)
    assert len(result.pages) == 3  # 0.30, 0.60, 0.90: the third crosses the cap, then stop
    assert len(fetcher.calls) == 3  # nothing fetched once the cap is hit
    assert {s.reason for s in result.skipped} == {"cost_cap"} and len(result.skipped) == 7
    assert result.status == "capped" and result.caps_hit == ["cost_cap"]


def test_already_exhausted_cost_cap_means_no_work() -> None:
    tracker = CostTracker(cap_usd=0.5)
    tracker.record(cost_usd=0.6, search_calls=0)
    fetcher = FakeFetcher()
    result = run([cite("https://a.example/p")], fetcher, tracker=tracker)
    assert fetcher.calls == [] and result.pages == []
    assert result.status == "capped" and result.skipped[0].reason == "cost_cap"


def test_cap_hit_inside_classification_keeps_the_page_and_stops() -> None:
    class Raising:
        async def generate(self, **kw: Any) -> LLMResult:
            raise CostCapExceeded(cap_usd=1, spent_usd=1)

    cs = [cite("https://a.example/p"), cite("https://b.example/p")]
    result = run(cs, FakeFetcher(), llm=Raising())
    assert len(result.pages) == 1
    p = result.pages[0]
    assert p.classification == "cost_cap" and p.evidence.source_type == "other"
    assert p.evidence.brand_on_page is False  # deterministic fields survive
    assert [s.reason for s in result.skipped] == ["cost_cap"]
    assert result.status == "capped"


# --- other rules ---------------------------------------------------------------------------------------


def test_prose_only_citations_are_not_fetched_but_are_reported() -> None:
    fetcher = FakeFetcher()
    result = run(
        [cite("https://a.example/p", source="answer_text"), cite("https://b.example/p")], fetcher
    )
    assert fetcher.calls == ["https://b.example/p"]
    assert [(s.url, s.reason) for s in result.skipped] == [
        ("https://a.example/p", "unverified_citation")
    ]
    assert result.status == "complete"  # not a cap


def test_duplicate_urls_are_fetched_once() -> None:
    cs = [
        cite("https://a.example/p"),
        cite("https://A.example/p/"),
        cite("http://www.a.example/p#frag"),
    ]
    fetcher = FakeFetcher()
    result = run(cs, fetcher)
    assert len(fetcher.calls) == 1 and len(result.pages) == 1
    assert result.duplicates_merged == 2


def test_a_crashing_fetcher_becomes_an_error_row_not_a_crashed_audit() -> None:
    class Boom:
        async def fetch(self, url: str) -> FetchResult:
            raise RuntimeError("kaboom")

    result = run([cite("https://a.example/p"), cite("https://b.example/p")], Boom())
    assert [p.evidence.fetch_status for p in result.pages] == ["error", "error"]
    assert all(p.evidence.brand_on_page is None for p in result.pages)


def test_empty_input_is_a_complete_empty_result() -> None:
    result = run([], FakeFetcher())
    assert result.pages == [] and result.status == "complete" and result.fetches_used == 0


def test_blocked_fetches_still_count_toward_the_budget() -> None:
    fetcher = FakeFetcher(status="blocked")
    result = run([cite(f"https://d{i}.example/") for i in range(31)], fetcher)
    assert len(fetcher.calls) == 30 and result.status == "capped"
