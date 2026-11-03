import inspect
from datetime import UTC, datetime
from typing import Any

import pytest

from app.schemas import Citation, Mention
from app.verify import verifier
from app.verify.verifier import (
    Code,
    VerificationReport,
    extract_prose_urls,
    find_name,
    normalize,
    strip_urls,
    verify_mention,
)

NOW = datetime(2026, 11, 3, tzinfo=UTC)


def grounded(url: str, title: str | None = None) -> Citation:
    host = url.split("//", 1)[-1].split("/", 1)[0]
    return Citation(
        url=url, domain=host, title=title, source="grounding_metadata", retrieved_at=NOW
    )


def prose(url: str) -> Citation:
    host = url.split("//", 1)[-1].split("/", 1)[0]
    return Citation(url=url, domain=host, source="answer_text", retrieved_at=NOW)


def mention(**kw: Any) -> Mention:
    data: dict[str, Any] = {
        "brand_mentioned": True,
        "position": 1,
        "quote": "HubSpot is great",
        "competitors_seen": [],
        "citations": [],
        "sentiment": "pos",
    }
    data.update(kw)
    return Mention(**data)


def verify(
    m: Mention,
    answer: str,
    *,
    brand: str = "HubSpot",
    aliases: list[str] | None = None,
    competitors: list[str] | None = None,
) -> VerificationReport:
    return verify_mention(
        m,
        raw_answer=answer,
        brand=brand,
        aliases=aliases or [],
        competitors=competitors or [],
        now=NOW,
    )


def codes(report: VerificationReport, status: str | None = None) -> list[Code]:
    return [f.code for f in report.findings if status is None or f.status == status]


# ---------------------------------------------------------------- normalisation


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HubSpot", "hubspot"),
        ("  Hub   Spot \n\t CRM ", "hub spot crm"),
        ("Hello, world!", "hello world"),
        ("“Smart” quotes — and dashes", "smart quotes and dashes"),
        ("Hub​Spot", "hubspot"),  # zero-width space is dropped, not turned into a gap
        ("non breaking", "non breaking"),
        ("ＨｕｂＳｐｏｔ", "hubspot"),  # full-width letters (NFKC)
        ("**bold** and _italic_ and `code`", "bold and italic and code"),
        ("", ""),
        ("...", ""),
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


def test_normalize_is_idempotent() -> None:
    s = "  Acme, Inc. — the “best”!  "
    assert normalize(normalize(s)) == normalize(s)


def test_find_name_uses_word_boundaries() -> None:
    assert find_name("i love hubspot today", "HubSpot") is not None
    assert find_name("hubspotty things", "HubSpot") is None
    assert find_name("the hub spot", "HubSpot") is None  # needs an alias, by design
    assert find_name("a hubspot", "HubSpot") is not None
    assert find_name("hubspot", "HubSpot") is not None
    assert find_name("anything", "!!!") is None  # name with nothing searchable


# ---------------------------------------------------------------- brand spelling


@pytest.mark.parametrize(
    "answer",
    [
        "Hubspot is great for small teams.",
        "HUBSPOT is great for small teams.",
        "hubspot is great for small teams.",
        "HubSpot is great for small teams.",
    ],
)
def test_hubspot_case_variants_all_verify(answer: str) -> None:
    report = verify(mention(quote="HubSpot is great"), answer)
    assert report.verdict.status == "verified", report.verdict.reason


def test_brand_given_in_other_case_than_the_answer() -> None:
    report = verify(mention(quote="HubSpot is great"), "HubSpot is great.", brand="Hubspot")
    assert report.verdict.status == "verified"


def test_brand_that_is_only_a_prefix_of_another_word_is_not_found() -> None:
    answer = "HubSpotty is a different thing."
    report = verify(mention(quote="HubSpotty is a different thing"), answer)
    assert report.verdict.status == "flagged"
    assert Code.BRAND_NOT_IN_TEXT in codes(report, "flagged")


def test_alias_in_text_verifies_the_mention() -> None:
    answer = "Hub Spot offers a free CRM."
    report = verify(
        mention(quote="Hub Spot offers a free CRM"), answer, aliases=["HubSpot CRM", "Hub Spot"]
    )
    assert report.verdict.status == "verified", report.verdict.reason
    assert "Hub Spot" in report.verdict.reason  # says which name matched


def test_alias_in_quote_only_is_enough() -> None:
    answer = "Many teams pick HubSpot CRM first, and Rival second."
    report = verify(
        mention(quote="HubSpot CRM first", competitors_seen=["Rival"], position=1),
        answer,
        aliases=["HubSpot CRM"],
        competitors=["Rival"],
    )
    assert report.verdict.status == "verified", report.verdict.reason


def test_alias_does_not_match_inside_other_words() -> None:
    answer = "The Hubble telescope is not software."
    report = verify(mention(quote="Hubble telescope"), answer, aliases=["Hub"])
    assert Code.BRAND_NOT_IN_TEXT in codes(report, "flagged")


# ---------------------------------------------------------------- brand only inside a URL


def test_brand_only_inside_a_full_url_is_not_a_mention() -> None:
    answer = "For pricing see https://hubspot.com/pricing today."
    report = verify(mention(quote="https://hubspot.com/pricing"), answer)
    assert report.verdict.status == "flagged"
    assert Code.BRAND_ONLY_IN_URL in codes(report, "flagged")
    assert "URL" in report.verdict.reason


def test_brand_only_inside_a_bare_domain_is_not_a_mention() -> None:
    answer = "Check hubspot.com for details."
    report = verify(mention(quote="Check hubspot.com"), answer)
    assert Code.BRAND_ONLY_IN_URL in codes(report, "flagged")


def test_brand_only_inside_a_www_url_is_not_a_mention() -> None:
    answer = "Docs live at www.hubspot.com/docs."
    report = verify(mention(quote="www.hubspot.com/docs"), answer)
    assert Code.BRAND_ONLY_IN_URL in codes(report, "flagged")


def test_not_mentioned_claim_is_confirmed_when_brand_only_appears_in_a_url() -> None:
    answer = "Try Rival. More at https://hubspot.com/compare."
    m = mention(brand_mentioned=False, position=None, quote=None, sentiment="neu")
    report = verify(m, answer, competitors=["Rival"])
    assert Code.NEGATIVE_CONFIRMED in codes(report, "verified")
    assert Code.PROSE_URL_UNVERIFIED in codes(report, "flagged")  # the URL itself is still flagged


def test_brand_that_is_itself_a_domain_is_still_matched_in_prose() -> None:
    answer = "Monday.com is a popular work OS."
    report = verify(mention(quote="Monday.com is a popular work OS"), answer, brand="Monday.com")
    assert report.verdict.status == "verified", report.verdict.reason


def test_brand_next_to_a_url_in_the_same_sentence_still_counts() -> None:
    answer = "HubSpot (https://hubspot.com) is great."
    report = verify(mention(quote="HubSpot"), answer)
    assert Code.BRAND_FOUND in codes(report, "verified")


def test_strip_urls_keeps_surrounding_words() -> None:
    out = strip_urls("see https://a.com/x and www.b.org, then c.io/path ok", names=[])
    assert "a.com" not in out and "b.org" not in out and "c.io" not in out
    assert "see" in out and "then" in out and "ok" in out


# ---------------------------------------------------------------- quotes


def test_exact_quote_verifies() -> None:
    report = verify(mention(quote="HubSpot is great"), "Honestly, HubSpot is great for CRM.")
    assert report.verdict.status == "verified"
    assert Code.QUOTE_VERIFIED in codes(report, "verified")


def test_quote_matching_ignores_case_whitespace_and_punctuation() -> None:
    answer = "Honestly,   HubSpot\nis GREAT -- for CRM!"
    report = verify(mention(quote="hubspot is great for crm"), answer)
    assert Code.QUOTE_VERIFIED in codes(report, "verified")


def test_quote_with_smart_quotes_vs_ascii() -> None:
    answer = "People say “HubSpot isn’t cheap” often."
    report = verify(mention(quote='"HubSpot isn\'t cheap"'), answer)
    assert Code.QUOTE_VERIFIED in codes(report, "verified")


def test_paraphrased_quote_is_flagged() -> None:
    answer = "HubSpot is great for small teams."
    report = verify(mention(quote="HubSpot works well for small businesses"), answer)
    assert report.verdict.status == "flagged"
    assert Code.QUOTE_NOT_FOUND in codes(report, "flagged")
    assert "HubSpot works well for small businesses" in report.verdict.reason


def test_quote_stitched_from_two_passages_is_flagged() -> None:
    answer = "HubSpot is great. Many other tools exist. Pricing varies widely."
    report = verify(mention(quote="HubSpot is great. Pricing varies widely."), answer)
    assert Code.QUOTE_NOT_FOUND in codes(report, "flagged")


@pytest.mark.parametrize(
    ("answer", "quote"),
    [
        ("HubSpot is greater than most.", "HubSpot is great"),  # quote ends mid-word
        ("HubSpot is great.", "Spot is great"),  # quote starts mid-word
        ("HubSpot is great.", "HubSpot is gre"),  # truncated
    ],
)
def test_quote_must_match_whole_words(answer: str, quote: str) -> None:
    report = verify(mention(quote=quote), answer)
    assert Code.QUOTE_NOT_FOUND in codes(report, "flagged")
    assert report.verdict.status == "flagged"


def test_quote_matching_is_case_insensitive_for_non_ascii_too() -> None:
    answer = "STRASSE und HubSpot sind gut."
    report = verify(mention(quote="strasse und hubspot"), answer)
    assert Code.QUOTE_VERIFIED in codes(report, "verified")
    answer2 = "Die Straße und HubSpot."  # sharp s folds to "ss"
    report2 = verify(mention(quote="STRASSE UND HUBSPOT"), answer2)
    assert Code.QUOTE_VERIFIED in codes(report2, "verified")


def test_quote_with_extra_word_is_flagged() -> None:
    report = verify(mention(quote="HubSpot is really great"), "HubSpot is great.")
    assert Code.QUOTE_NOT_FOUND in codes(report, "flagged")


def test_quote_that_is_real_but_does_not_contain_the_brand_is_flagged() -> None:
    answer = "HubSpot is great. It is also reasonably priced."
    report = verify(mention(quote="It is also reasonably priced"), answer)
    assert Code.QUOTE_NO_BRAND in codes(report, "flagged")


def test_quote_that_is_only_punctuation_is_flagged_not_trivially_matched() -> None:
    # An empty string is a substring of everything, so this must be caught explicitly.
    report = verify(mention(quote="..."), "HubSpot is great.")
    assert Code.QUOTE_EMPTY in codes(report, "flagged")
    assert Code.QUOTE_VERIFIED not in codes(report)


def test_missing_quote_on_a_mention_is_flagged() -> None:
    report = verify(mention(quote=None), "HubSpot is great.")
    assert Code.QUOTE_MISSING in codes(report, "flagged")
    assert report.verdict.status == "flagged"


def test_quote_must_come_from_the_raw_answer_not_the_citations() -> None:
    m = mention(quote="HubSpot leads the market", citations=[grounded("https://g2.com/hubspot")])
    report = verify(m, "HubSpot is great.")
    assert Code.QUOTE_NOT_FOUND in codes(report, "flagged")


@pytest.mark.parametrize("seed", range(5))
def test_every_word_aligned_slice_of_the_answer_verifies(seed: int) -> None:
    answer = (
        "In 2026, HubSpot -- the CRM platform -- is, frankly, a strong pick. "
        "Rival offers “more integrations”; Globex is cheaper.\nTry HubSpot first!"
    )
    words = answer.split(" ")
    n = len(words)
    checked = 0
    for start in range(seed, n):
        for end in range(start + 1, min(n, start + 6) + 1):
            quote = " ".join(words[start:end])
            if find_name(normalize(strip_urls(quote, [])), "HubSpot") is None:
                continue
            checked += 1
            report = verify(mention(quote=quote, position=None), answer)
            assert Code.QUOTE_VERIFIED in codes(report, "verified"), quote
    assert checked > 0


# ---------------------------------------------------------------- position


ANSWER_RANK = "Rival is popular. HubSpot is great for CRM. Globex is cheaper."


def test_position_matches_when_brand_is_first() -> None:
    report = verify(
        mention(position=1, quote="HubSpot is great"),
        "HubSpot is great. Rival is popular.",
        competitors=["Rival"],
    )
    assert Code.POSITION_VERIFIED in codes(report, "verified")


def test_position_matches_when_a_listed_competitor_comes_first() -> None:
    report = verify(
        mention(position=2, quote="HubSpot is great for CRM"),
        ANSWER_RANK,
        competitors=["Rival", "Globex"],
    )
    assert Code.POSITION_VERIFIED in codes(report, "verified"), report.verdict.reason
    assert report.computed_position == 2


def test_position_off_by_one_too_low_is_flagged() -> None:
    report = verify(
        mention(position=1, quote="HubSpot is great for CRM"),
        ANSWER_RANK,
        competitors=["Rival", "Globex"],
    )
    assert report.verdict.status == "flagged"
    assert Code.POSITION_CONTRADICTED in codes(report, "flagged")
    assert "Rival" in report.verdict.reason


def test_position_off_by_one_too_high_is_flagged() -> None:
    report = verify(
        mention(position=2, quote="HubSpot is great"),
        "HubSpot is great. Rival is popular.",
        competitors=["Rival"],
    )
    assert report.verdict.status == "flagged"
    assert Code.POSITION_UNVERIFIABLE in codes(report, "flagged")


def test_position_counts_only_competitors_that_actually_appear() -> None:
    report = verify(
        mention(position=1, quote="HubSpot is great"),
        "HubSpot is great.",
        competitors=["Rival", "Globex", "Initech"],
    )
    assert Code.POSITION_VERIFIED in codes(report, "verified")


def test_competitor_named_only_inside_a_url_does_not_change_the_rank() -> None:
    answer = "See https://rival.com/vs first. HubSpot is great."
    report = verify(mention(position=1, quote="HubSpot is great"), answer, competitors=["Rival"])
    assert Code.POSITION_VERIFIED in codes(report, "verified")


def test_position_uses_the_earliest_alias_occurrence() -> None:
    answer = "Rival is popular. Hub Spot is great. Later, HubSpot again."
    report = verify(
        mention(position=2, quote="Hub Spot is great"),
        answer,
        aliases=["Hub Spot"],
        competitors=["Rival"],
    )
    assert Code.POSITION_VERIFIED in codes(report, "verified"), report.verdict.reason


def test_no_position_claim_is_reported_not_silently_skipped() -> None:
    report = verify(mention(position=None), "HubSpot is great.")
    assert Code.POSITION_NOT_CLAIMED in codes(report)
    assert report.verdict.status == "verified"


# ---------------------------------------------------------------- empty answer, negatives


@pytest.mark.parametrize("answer", ["", "   ", "\n\t "])
def test_empty_answer_is_always_flagged(answer: str) -> None:
    report = verify(mention(), answer)
    assert report.verdict.status == "flagged"
    assert Code.ANSWER_EMPTY in codes(report, "flagged")


def test_empty_answer_is_flagged_even_for_a_not_mentioned_claim() -> None:
    # Absence of evidence in an empty answer is not verified absence.
    m = mention(brand_mentioned=False, position=None, quote=None, sentiment="neu")
    report = verify(m, "")
    assert report.verdict.status == "flagged"
    assert Code.ANSWER_EMPTY in codes(report, "flagged")


def test_not_mentioned_is_confirmed_when_brand_is_absent_from_the_text() -> None:
    m = mention(brand_mentioned=False, position=None, quote=None, sentiment="neu")
    report = verify(m, "Rival and Globex are both fine.", competitors=["Rival", "Globex"])
    assert report.verdict.status == "verified"
    assert Code.NEGATIVE_CONFIRMED in codes(report, "verified")


def test_not_mentioned_but_brand_text_present_is_flagged_for_review() -> None:
    # e.g. "Unlike HubSpot, Rival ..." - code cannot judge the semantics, so a human does.
    answer = "Rival is the top pick. Unlike HubSpot, Rival has 24/7 support."
    m = mention(brand_mentioned=False, position=None, quote=None, sentiment="neu")
    report = verify(m, answer, competitors=["Rival"])
    assert report.verdict.status == "flagged"
    assert Code.NEGATIVE_BRAND_PRESENT in codes(report, "flagged")


def test_not_mentioned_but_alias_present_is_flagged() -> None:
    m = mention(brand_mentioned=False, position=None, quote=None, sentiment="neu")
    report = verify(m, "Hub Spot was mentioned.", aliases=["Hub Spot"])
    assert Code.NEGATIVE_BRAND_PRESENT in codes(report, "flagged")


# ---------------------------------------------------------------- competitors, sentiment


def test_claimed_competitor_missing_from_text_is_flagged() -> None:
    report = verify(
        mention(competitors_seen=["Rival", "Globex"]),
        "HubSpot is great. Rival is fine.",
        competitors=["Rival", "Globex"],
    )
    assert Code.COMPETITOR_NOT_IN_TEXT in codes(report, "flagged")
    assert "Globex" in report.verdict.reason
    assert Code.COMPETITOR_VERIFIED in codes(report, "verified")


def test_sentiment_is_reported_as_not_checked_and_does_not_flag() -> None:
    report = verify(mention(sentiment="neg"), "HubSpot is great.")
    sentiment = [f for f in report.findings if f.code == Code.SENTIMENT_NOT_CHECKED]
    assert len(sentiment) == 1
    assert sentiment[0].status == "not_checked"
    assert report.verdict.status == "verified"


# ---------------------------------------------------------------- citations


def test_grounded_citation_is_verified() -> None:
    m = mention(citations=[grounded("https://g2.com/hubspot")])
    report = verify(m, "HubSpot is great.")
    assert Code.CITATION_VERIFIED in codes(report, "verified")
    assert report.verdict.status == "verified"


def test_url_in_metadata_but_not_in_text_is_still_verified() -> None:
    m = mention(citations=[grounded("https://only-in-metadata.example/page")])
    report = verify(m, "HubSpot is great. No links here.")
    assert report.verdict.status == "verified", report.verdict.reason
    cit = [f for f in report.findings if f.code == Code.CITATION_VERIFIED]
    assert "not referenced in the answer text" in cit[0].reason
    assert report.prose_citations == []


def test_url_only_in_prose_is_flagged_as_unverified_citation() -> None:
    answer = "HubSpot is great, see https://blog.example/hubspot-review for more."
    report = verify(mention(), answer)
    assert report.verdict.status == "flagged"
    assert Code.PROSE_URL_UNVERIFIED in codes(report, "flagged")
    assert "https://blog.example/hubspot-review" in report.verdict.reason
    assert len(report.prose_citations) == 1
    c = report.prose_citations[0]
    assert c.source == "answer_text"
    assert c.unverified is True
    assert c.domain == "blog.example"
    assert c.retrieved_at == NOW


def test_prose_url_that_is_also_in_metadata_is_verified() -> None:
    m = mention(citations=[grounded("https://blog.example/hubspot-review")])
    report = verify(m, "HubSpot is great (https://blog.example/hubspot-review).")
    assert report.verdict.status == "verified", report.verdict.reason
    assert report.prose_citations == []


@pytest.mark.parametrize(
    "prose_url",
    [
        "https://blog.example/post.",  # trailing sentence punctuation
        "http://blog.example/post",  # scheme differs
        "https://www.blog.example/post",  # www differs
        "https://BLOG.example/post/",  # host case and trailing slash
        "https://blog.example/post#section",  # fragment
    ],
)
def test_prose_url_variants_match_the_metadata_url(prose_url: str) -> None:
    m = mention(citations=[grounded("https://blog.example/post")])
    report = verify(m, f"HubSpot is great, per {prose_url} and others.")
    assert Code.PROSE_URL_UNVERIFIED not in codes(report), prose_url
    assert report.verdict.status == "verified", report.verdict.reason


def test_prose_url_with_a_different_path_is_not_matched() -> None:
    m = mention(citations=[grounded("https://blog.example/post-a")])
    report = verify(m, "HubSpot is great, per https://blog.example/post-b.")
    assert Code.PROSE_URL_UNVERIFIED in codes(report, "flagged")


def test_markdown_link_target_is_checked_too() -> None:
    answer = "HubSpot is [great](https://blog.example/md) for CRM."
    report = verify(mention(quote="HubSpot is great"), answer)
    assert Code.PROSE_URL_UNVERIFIED in codes(report, "flagged")


def test_each_prose_url_is_flagged_once_even_if_repeated() -> None:
    answer = "HubSpot is great https://x.example/a and again https://x.example/a/ ok"
    report = verify(mention(quote="HubSpot is great"), answer)
    assert codes(report).count(Code.PROSE_URL_UNVERIFIED) == 1
    assert len(report.prose_citations) == 1


def test_a_citation_carried_on_the_mention_with_answer_text_source_is_flagged() -> None:
    m = mention(citations=[prose("https://blog.example/claimed")])
    report = verify(m, "HubSpot is great.")
    assert Code.CITATION_UNVERIFIED in codes(report, "flagged")
    assert report.verdict.status == "flagged"


def test_extract_prose_urls_strips_trailing_punctuation_and_dedupes() -> None:
    urls = extract_prose_urls(
        "a https://x.example/a, b (https://y.example/b). c https://x.example/a"
    )
    assert urls == ["https://x.example/a", "https://y.example/b"]


# ---------------------------------------------------------------- verdict shape, nothing dropped


def test_every_claim_gets_a_finding() -> None:
    m = mention(
        position=1,
        quote="HubSpot is great",
        competitors_seen=["Rival"],
        citations=[grounded("https://g2.com/a"), grounded("https://g2.com/b")],
    )
    report = verify(m, "HubSpot is great. Rival too.", competitors=["Rival"])
    got = codes(report)
    for expected in (
        Code.BRAND_FOUND,
        Code.QUOTE_VERIFIED,
        Code.POSITION_VERIFIED,
        Code.COMPETITOR_VERIFIED,
        Code.SENTIMENT_NOT_CHECKED,
    ):
        assert expected in got
    assert got.count(Code.CITATION_VERIFIED) == 2


def test_multiple_problems_are_all_reported_in_the_reason() -> None:
    m = mention(quote="made up words", position=3, competitors_seen=["Ghost"])
    answer = "HubSpot is great. See https://z.example/q"
    report = verify(m, answer, competitors=["Ghost"])
    assert report.verdict.status == "flagged"
    flagged = set(codes(report, "flagged"))
    assert {Code.QUOTE_NOT_FOUND, Code.COMPETITOR_NOT_IN_TEXT, Code.PROSE_URL_UNVERIFIED} <= flagged
    for needle in ("made up words", "Ghost", "https://z.example/q"):
        assert needle in report.verdict.reason


def test_verdict_reason_is_never_blank() -> None:
    for m, ans in [
        (mention(), "HubSpot is great."),
        (mention(), ""),
        (mention(quote="nope"), "HubSpot is great."),
    ]:
        assert verify(m, ans).verdict.reason.strip()


def test_verification_is_deterministic() -> None:
    m = mention(citations=[grounded("https://g2.com/a")])
    answer = "HubSpot is great. See https://z.example/q"
    a = verify(m, answer).model_dump()
    b = verify(m, answer).model_dump()
    assert a == b


def test_inputs_are_not_mutated() -> None:
    m = mention(citations=[grounded("https://g2.com/a")])
    before = m.model_dump()
    answer = "HubSpot is great."
    verify(m, answer)
    assert m.model_dump() == before
    assert answer == "HubSpot is great."


def test_verifier_is_pure_python_with_no_llm_dependency() -> None:
    source = inspect.getsource(verifier)
    assert "app.llm" not in source
    assert "httpx" not in source
    assert not any(name.startswith("llm") for name in vars(verifier))
