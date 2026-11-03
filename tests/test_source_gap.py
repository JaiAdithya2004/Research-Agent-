from datetime import UTC, datetime
from typing import Any, Literal

from app.research.gaps import compute_source_gap
from app.schemas import Citation, Mention

NOW = datetime(2026, 11, 3, tzinfo=UTC)


def c(
    domain: str,
    source: Literal["grounding_metadata", "answer_text"] = "grounding_metadata",
    path: str = "/p",
) -> Citation:
    return Citation(
        url=f"https://{domain}{path}",
        domain=domain,
        source=source,
        retrieved_at=NOW,
    )


def m(
    *,
    brand: bool = False,
    competitors: list[str] | None = None,
    cites: list[Citation] | None = None,
    **kw: Any,
) -> Mention:
    return Mention(
        brand_mentioned=brand,
        position=1 if brand else None,
        quote="Acme" if brand else None,
        competitors_seen=competitors or [],
        citations=cites or [],
        sentiment="pos" if brand else "neu",
        **kw,
    )


def test_domain_cited_only_for_competitors_is_a_gap() -> None:
    report = compute_source_gap([m(competitors=["Rival"], cites=[c("g2.com")])])
    assert [(e.domain, e.frequency, e.competitors) for e in report.entries] == [
        ("g2.com", 1, ["Rival"])
    ]


def test_domain_also_cited_for_the_brand_is_not_a_gap() -> None:
    report = compute_source_gap(
        [
            m(competitors=["Rival"], cites=[c("g2.com"), c("capterra.com")]),
            m(brand=True, cites=[c("g2.com")]),
        ]
    )
    assert [e.domain for e in report.entries] == ["capterra.com"]


def test_an_answer_mentioning_both_counts_as_cited_for_the_brand() -> None:
    report = compute_source_gap([m(brand=True, competitors=["Rival"], cites=[c("g2.com")])])
    assert report.entries == []


def test_ranked_by_frequency_then_domain() -> None:
    mentions = [
        m(competitors=["Rival"], cites=[c("b.com"), c("a.com"), c("z.com")]),
        m(competitors=["Rival"], cites=[c("z.com")]),
        m(competitors=["Globex"], cites=[c("z.com"), c("a.com")]),
    ]
    report = compute_source_gap(mentions)
    assert [(e.domain, e.frequency) for e in report.entries] == [
        ("z.com", 3),
        ("a.com", 2),
        ("b.com", 1),
    ]


def test_frequency_counts_answers_not_repeated_citations_in_one_answer() -> None:
    report = compute_source_gap(
        [m(competitors=["Rival"], cites=[c("a.com", path="/1"), c("a.com", path="/2")])]
    )
    assert report.entries[0].frequency == 1


def test_competitors_are_collected_sorted_and_unique() -> None:
    mentions = [
        m(competitors=["Rival"], cites=[c("a.com")]),
        m(competitors=["Globex", "Rival"], cites=[c("a.com")]),
    ]
    assert compute_source_gap(mentions).entries[0].competitors == ["Globex", "Rival"]


def test_prose_only_citations_do_not_count() -> None:
    report = compute_source_gap(
        [m(competitors=["Rival"], cites=[c("a.com", source="answer_text")])]
    )
    assert report.entries == []


def test_no_competitor_mentions_means_no_gap() -> None:
    assert (
        compute_source_gap([m(cites=[c("a.com")]), m(brand=True, cites=[c("b.com")])]).entries == []
    )


def test_empty_input() -> None:
    assert compute_source_gap([]).entries == []


def test_domain_case_and_www_are_normalised() -> None:
    mentions = [
        m(competitors=["Rival"], cites=[c("WWW.G2.com")]),
        m(competitors=["Rival"], cites=[c("g2.com")]),
        m(brand=True, cites=[c("www.capterra.com")]),
        m(competitors=["Rival"], cites=[c("Capterra.com")]),
    ]
    report = compute_source_gap(mentions)
    assert [(e.domain, e.frequency) for e in report.entries] == [("g2.com", 2)]


def test_the_report_is_a_valid_ranked_source_gap_report() -> None:
    mentions = [m(competitors=["Rival"], cites=[c(f"d{i}.com")]) for i in range(5)]
    report = compute_source_gap(mentions)
    assert [e.frequency for e in report.entries] == sorted(
        (e.frequency for e in report.entries), reverse=True
    )


def test_inputs_are_not_mutated_and_result_is_deterministic() -> None:
    mentions = [m(competitors=["Rival"], cites=[c("a.com"), c("b.com")])]
    before = [x.model_dump() for x in mentions]
    a = compute_source_gap(mentions).model_dump()
    b = compute_source_gap(mentions).model_dump()
    assert a == b and [x.model_dump() for x in mentions] == before


def test_module_is_pure_python_with_no_model_dependency() -> None:
    import inspect

    from app.research import gaps

    source = inspect.getsource(gaps)
    for banned in ("app.llm", "httpx", "app.fetch"):
        assert banned not in source
