"""Deterministic "is the brand on this page" check. No model: the quote comes from the page."""

import pytest

from app.verify import page_check
from app.verify.page_check import check_brand_on_page
from app.verify.verifier import Code, normalize, verify_quote

PAGE = (
    "Project tools compared. Rival is cheap and popular. HubSpot offers a free CRM for small "
    "teams, and many reviewers like it. Globex targets enterprises."
)


def check(text: str, brand: str = "HubSpot", aliases: list[str] | None = None, **kw: object):  # type: ignore[no-untyped-def]
    return check_brand_on_page(text, brand=brand, aliases=aliases or [], **kw)  # type: ignore[arg-type]


def test_brand_on_page_returns_a_verbatim_sentence_as_the_quote() -> None:
    result = check(PAGE)
    assert result.found is True
    assert result.quote == "HubSpot offers a free CRM for small teams, and many reviewers like it."
    assert result.quote in PAGE  # verbatim, not normalised
    assert result.matched_name == "HubSpot"


def test_the_quote_passes_the_same_verifier_used_for_answers() -> None:
    result = check(PAGE)
    assert result.quote is not None
    finding = verify_quote(result.quote, PAGE, ["HubSpot"])
    assert finding.code == Code.QUOTE_VERIFIED and finding.status == "verified"


@pytest.mark.parametrize("spelling", ["Hubspot", "HUBSPOT", "hubspot"])
def test_case_variants_are_found(spelling: str) -> None:
    result = check(f"We tried {spelling} last year and liked it.")
    assert result.found is True
    assert result.quote is not None and spelling in result.quote


def test_alias_is_found_and_reported() -> None:
    result = check("Hub Spot is a popular CRM.", aliases=["Hub Spot"])
    assert result.found is True
    assert result.matched_name == "Hub Spot"


def test_brand_absent_is_false_with_no_quote() -> None:
    result = check("Rival and Globex are fine choices.")
    assert result.found is False
    assert result.quote is None


def test_brand_that_is_a_prefix_of_another_word_is_absent() -> None:
    assert check("HubSpotty is not the same thing.").found is False
    assert check("The Hubble telescope.", aliases=["Hub"]).found is False


@pytest.mark.parametrize(
    "text",
    [
        "See https://hubspot.com/pricing for details about plans and so on.",
        "Visit hubspot.com today for details about plans and so on.",
        "Docs: www.hubspot.com/docs for details about plans and so on.",
    ],
)
def test_brand_only_inside_a_url_is_not_on_the_page(text: str) -> None:
    result = check(text)
    assert result.found is False
    assert result.quote is None
    assert "URL" in result.reason


def test_brand_in_prose_next_to_a_url_still_counts() -> None:
    result = check("HubSpot (https://hubspot.com) is a CRM that teams use.")
    assert result.found is True
    assert result.quote is not None and "HubSpot" in result.quote


def test_brand_that_is_itself_a_domain_is_found_in_prose() -> None:
    result = check("Monday.com is a work OS used by many teams.", brand="Monday.com")
    assert result.found is True


def test_first_occurrence_is_used_and_the_result_is_deterministic() -> None:
    text = "HubSpot is first here. Later HubSpot appears again."
    a, b = check(text), check(text)
    assert a == b
    assert a.quote == "HubSpot is first here."


def test_long_sentences_are_trimmed_around_the_brand_and_still_verify() -> None:
    filler = "word " * 200
    text = f"{filler}and then HubSpot appears in the middle of a very long run on sentence {filler}end."
    result = check(text)
    assert result.found is True
    assert result.quote is not None
    assert len(result.quote) <= page_check.SNIPPET_MAX + 40
    assert "HubSpot" in result.quote
    assert normalize(result.quote) in normalize(text)


def test_text_without_sentence_punctuation_still_works() -> None:
    result = check("navigation home about HubSpot pricing contact")
    assert result.found is True


def test_unicode_and_zero_width_characters_are_handled() -> None:
    result = check("We love Hub​Spot a lot, truly.")
    assert result.found is True
    result2 = check("ＨｕｂＳｐｏｔ is great for teams.")
    assert result2.found is True


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_blank_page_text_cannot_prove_absence(text: str) -> None:
    result = check(text)
    assert result.found is None
    assert result.quote is None


def test_competitor_domains_do_not_hide_the_brand_in_prose() -> None:
    result = check(
        "Rival.com vs HubSpot: which CRM wins for small teams?", competitors=["Rival.com"]
    )
    assert result.found is True


def test_if_the_quote_cannot_be_verified_the_answer_is_unknown_not_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(page_check, "_snippet", lambda *a, **k: "text that is not on the page")
    result = check(PAGE)
    assert result.found is None
    assert result.quote is None
    assert "needs review" in result.reason


def test_result_is_never_true_without_a_quote() -> None:
    for text in (PAGE, "HubSpot", "x HubSpot y", "Hubspot, hubspot, HUBSPOT"):
        result = check(text)
        assert (result.found is True) == (result.quote is not None)
