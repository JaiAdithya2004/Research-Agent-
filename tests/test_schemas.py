from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas import (
    AuditRequest,
    AuditSummary,
    Citation,
    Mention,
    Review,
    SourceEvidence,
    SourceGapEntry,
    SourceGapReport,
    Verdict,
)

NOW = datetime(2026, 11, 3, tzinfo=UTC)


def make_request(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "brand": "Acme",
        "category": "project management software",
        "questions": ["What is the best project management tool?"],
    }
    data.update(overrides)
    return data


# --- AuditRequest ---------------------------------------------------------


def test_audit_request_defaults() -> None:
    req = AuditRequest(**make_request())
    assert req.repeats == 3
    assert req.search_mode == "grounded"
    assert req.research_depth == "cited_pages"
    assert req.aliases == []
    assert req.competitors == []


def test_audit_request_accepts_exactly_ten_questions() -> None:
    req = AuditRequest(**make_request(questions=[f"q{i}" for i in range(10)]))
    assert len(req.questions) == 10


def test_audit_request_rejects_eleven_questions() -> None:
    with pytest.raises(ValidationError):
        AuditRequest(**make_request(questions=[f"q{i}" for i in range(11)]))


def test_audit_request_rejects_no_questions() -> None:
    with pytest.raises(ValidationError):
        AuditRequest(**make_request(questions=[]))


def test_audit_request_rejects_blank_question() -> None:
    with pytest.raises(ValidationError):
        AuditRequest(**make_request(questions=["   "]))


@pytest.mark.parametrize("repeats", [1, 3, 5])
def test_audit_request_accepts_valid_repeats(repeats: int) -> None:
    assert AuditRequest(**make_request(repeats=repeats)).repeats == repeats


@pytest.mark.parametrize("repeats", [0, 6, -1])
def test_audit_request_rejects_out_of_range_repeats(repeats: int) -> None:
    with pytest.raises(ValidationError):
        AuditRequest(**make_request(repeats=repeats))


@pytest.mark.parametrize("brand", ["", "   ", "\t\n"])
def test_audit_request_rejects_empty_brand(brand: str) -> None:
    with pytest.raises(ValidationError):
        AuditRequest(**make_request(brand=brand))


def test_audit_request_strips_brand() -> None:
    assert AuditRequest(**make_request(brand="  Acme ")).brand == "Acme"


@pytest.mark.parametrize("mode", ["off", "grounded", "both", "tavily"])
def test_audit_request_accepts_search_modes(mode: str) -> None:
    assert AuditRequest(**make_request(search_mode=mode)).search_mode == mode


@pytest.mark.parametrize("mode", ["on", "GROUNDED", "", "web"])
def test_audit_request_rejects_bad_search_mode(mode: str) -> None:
    with pytest.raises(ValidationError):
        AuditRequest(**make_request(search_mode=mode))


@pytest.mark.parametrize("depth", ["none", "cited_pages"])
def test_audit_request_accepts_research_depths(depth: str) -> None:
    assert AuditRequest(**make_request(research_depth=depth)).research_depth == depth


def test_audit_request_rejects_bad_research_depth() -> None:
    with pytest.raises(ValidationError):
        AuditRequest(**make_request(research_depth="deep"))


def test_audit_request_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        AuditRequest(**make_request(surprise=1))


# --- Citation / Mention ---------------------------------------------------


def make_citation(**overrides: Any) -> Citation:
    data: dict[str, Any] = {
        "url": "https://example.com/a",
        "domain": "example.com",
        "title": "A page",
        "source": "grounding_metadata",
        "retrieved_at": NOW,
    }
    data.update(overrides)
    return Citation(**data)


def test_citation_from_grounding_is_not_unverified() -> None:
    assert make_citation().unverified is False


def test_citation_from_answer_text_is_unverified() -> None:
    assert make_citation(source="answer_text").unverified is True


def test_citation_rejects_unknown_source() -> None:
    with pytest.raises(ValidationError):
        make_citation(source="prose")


def test_mention_not_mentioned_has_no_position() -> None:
    m = Mention(brand_mentioned=False, sentiment="neu")
    assert m.position is None
    assert m.quote is None
    assert m.competitors_seen == []


def test_mention_rejects_position_when_not_mentioned() -> None:
    with pytest.raises(ValidationError):
        Mention(brand_mentioned=False, position=1, sentiment="neu")


def test_mention_rejects_zero_position() -> None:
    with pytest.raises(ValidationError):
        Mention(brand_mentioned=True, position=0, sentiment="pos")


def test_mention_rejects_bad_sentiment() -> None:
    with pytest.raises(ValidationError):
        Mention(brand_mentioned=True, sentiment="happy")  # type: ignore[arg-type]


# --- SourceEvidence -------------------------------------------------------


def make_evidence(**overrides: Any) -> SourceEvidence:
    data: dict[str, Any] = {
        "url": "https://example.com/a",
        "fetch_status": "ok",
        "brand_on_page": True,
        "quote": "Acme is great",
        "source_type": "review",
    }
    data.update(overrides)
    return SourceEvidence(**data)


def test_evidence_ok_page_can_have_brand_found_or_absent() -> None:
    assert make_evidence(brand_on_page=True).brand_on_page is True
    assert make_evidence(brand_on_page=False, quote=None).brand_on_page is False


@pytest.mark.parametrize("status", ["blocked", "unreachable", "paywalled", "too_large", "error"])
def test_evidence_unfetched_page_must_have_null_brand_on_page(status: str) -> None:
    with pytest.raises(ValidationError):
        make_evidence(fetch_status=status, brand_on_page=False, quote=None)
    ok = make_evidence(fetch_status=status, brand_on_page=None, quote=None)
    assert ok.brand_on_page is None


def test_evidence_quote_requires_brand_found() -> None:
    with pytest.raises(ValidationError):
        make_evidence(brand_on_page=False, quote="Acme")


def test_evidence_rejects_bad_status_and_source_type() -> None:
    with pytest.raises(ValidationError):
        make_evidence(fetch_status="missing")
    with pytest.raises(ValidationError):
        make_evidence(source_type="blog")


# --- Verdict / Review -----------------------------------------------------


def test_verdict_requires_reason() -> None:
    assert Verdict(status="verified", reason="quote found in raw answer").status == "verified"
    with pytest.raises(ValidationError):
        Verdict(status="flagged", reason="")
    with pytest.raises(ValidationError):
        Verdict(status="verified", reason="  ")


def test_verdict_rejects_unknown_status() -> None:
    with pytest.raises(ValidationError):
        Verdict(status="maybe", reason="x")  # type: ignore[arg-type]


def test_review_correct_requires_corrected_value() -> None:
    with pytest.raises(ValidationError):
        Review(action="correct", reason="wrong", reviewer="sam", timestamp=NOW)
    r = Review(
        action="correct", corrected_value="neg", reason="wrong", reviewer="sam", timestamp=NOW
    )
    assert r.corrected_value == "neg"


def test_review_accept_needs_no_corrected_value() -> None:
    r = Review(action="approve", reason="looks right", reviewer="sam", timestamp=NOW)
    assert r.corrected_value is None


def test_review_rejects_unknown_action_and_blank_reviewer() -> None:
    with pytest.raises(ValidationError):
        Review(action="delete", reason="x", reviewer="sam", timestamp=NOW)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Review(action="approve", reason="x", reviewer=" ", timestamp=NOW)


# --- AuditSummary / SourceGapReport --------------------------------------


def make_summary(**overrides: Any) -> AuditSummary:
    data: dict[str, Any] = {
        "mention_rate": 0.5,
        "avg_position": 2.0,
        "stability": 0.8,
        "flagged_count": 1,
        "total_cost_usd": 0.12,
        "search_calls": 4,
        "pages_fetched": 6,
        "latency_p50_ms": 900,
        "latency_p90_ms": 2000,
        "status": "complete",
    }
    data.update(overrides)
    return AuditSummary(**data)


def test_summary_defaults() -> None:
    s = make_summary()
    assert s.mode_delta is None
    assert s.degraded is False


@pytest.mark.parametrize("status", ["running", "complete", "capped", "failed"])
def test_summary_accepts_statuses(status: str) -> None:
    assert make_summary(status=status).status == status


def test_summary_rejects_bad_status_and_ranges() -> None:
    with pytest.raises(ValidationError):
        make_summary(status="done")
    with pytest.raises(ValidationError):
        make_summary(mention_rate=1.5)
    with pytest.raises(ValidationError):
        make_summary(stability=-0.1)
    with pytest.raises(ValidationError):
        make_summary(total_cost_usd=-1)


def test_summary_p90_cannot_be_below_p50() -> None:
    with pytest.raises(ValidationError):
        make_summary(latency_p50_ms=3000, latency_p90_ms=1000)


def test_gap_report_holds_ranked_entries() -> None:
    report = SourceGapReport(
        entries=[
            SourceGapEntry(domain="g2.com", frequency=5, competitors=["Rival"]),
            SourceGapEntry(domain="reddit.com", frequency=2, competitors=["Rival"]),
        ]
    )
    assert [e.domain for e in report.entries] == ["g2.com", "reddit.com"]


def test_gap_report_rejects_unranked_entries() -> None:
    with pytest.raises(ValidationError):
        SourceGapReport(
            entries=[
                SourceGapEntry(domain="reddit.com", frequency=2, competitors=["Rival"]),
                SourceGapEntry(domain="g2.com", frequency=5, competitors=["Rival"]),
            ]
        )
