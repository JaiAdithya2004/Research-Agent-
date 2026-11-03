import pytest

from app.extract.prompts import PROMPTS_DIR
from app.llm.cost import CostCapExceeded
from app.llm.types import LLMCallFailed, LLMResult, ProviderRateLimited
from app.research.classifier import SourceClassifier, rule_source_type
from tests.conftest import run_async
from tests.test_extractor import llm_result

BRAND, ALIASES, COMPETITORS = "Acme", ["AcmeHQ"], ["Rival", "Globex Corp"]


class FakeLLM:
    def __init__(self, replies: list[str | Exception]) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []
        self.searches: list[bool] = []

    async def generate(
        self, *, provider: str, model: str, prompt: str, search: bool = False
    ) -> LLMResult:
        self.prompts.append(prompt)
        self.searches.append(search)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return llm_result(reply)


def classify(
    llm: FakeLLM | None, domain: str = "blog.example.org", text: str | None = "A page."
) -> object:
    classifier = SourceClassifier(llm, provider="fake", model="m")
    return run_async(
        lambda: classifier.classify(
            domain=domain, page_text=text, brand=BRAND, aliases=ALIASES, competitors=COMPETITORS
        )
    )


# --- rules ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("domain", "expected"),
    [
        ("acme.com", "own_site"),
        ("www.acme.com", "own_site"),
        ("blog.acme.io", "own_site"),
        ("acmehq.com", "own_site"),  # alias
        ("rival.com", "competitor"),
        ("docs.rival.co", "competitor"),
        ("globexcorp.com", "competitor"),  # multi-word competitor, spaces removed
        ("g2.com", "review"),
        ("www.capterra.com", "review"),
        ("trustpilot.com", "review"),
        ("reddit.com", "forum"),
        ("old.reddit.com", "forum"),
        ("news.ycombinator.com", "forum"),
        ("community.example.org", "forum"),
        ("forum.example.org", "forum"),
        ("reuters.com", "news"),
        ("techcrunch.com", "news"),
        ("blog.example.org", None),
        ("acme-fans.example.org", None),  # not an exact label match
        ("notacme.com", None),
    ],
)
def test_rule_source_type(domain: str, expected: str | None) -> None:
    assert rule_source_type(domain, BRAND, ALIASES, COMPETITORS) == expected


def test_own_site_wins_over_a_review_site_list() -> None:
    assert rule_source_type("g2.com", "G2", [], []) == "own_site"


def test_rules_answer_without_calling_the_model() -> None:
    llm = FakeLLM([])
    result = classify(llm, domain="g2.com")
    assert result.source_type == "review"  # type: ignore[attr-defined]
    assert result.method == "rules"  # type: ignore[attr-defined]
    assert llm.prompts == []


# --- model path ----------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["review", "news", "forum", "other"])
def test_model_answer_is_validated_and_returned(kind: str) -> None:
    llm = FakeLLM([f'{{"source_type": "{kind}"}}'])
    result = classify(llm)
    assert result.source_type == kind  # type: ignore[attr-defined]
    assert result.method == "model"  # type: ignore[attr-defined]


def test_fenced_json_is_accepted() -> None:
    result = classify(FakeLLM(['```json\n{"source_type": "news"}\n```']))
    assert result.source_type == "news"  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "reply",
    [
        "",
        "news",
        "{",
        "null",
        "[]",
        '{"source_type": "blog"}',
        '{"source_type": "NEWS"}',
        '{"source_type": "news", "extra": 1}',
        '{"type": "news"}',
        '{"source_type": ["news"]}',
        '{"source_type": "own_site"}',
        '{"source_type": "competitor"}',
    ],
)
def test_invalid_model_output_falls_back_to_other_visibly(reply: str) -> None:
    result = classify(FakeLLM([reply]))
    assert result.source_type == "other"  # type: ignore[attr-defined]
    assert result.method == "fallback"  # type: ignore[attr-defined]
    assert reply.strip() == "" or reply not in result.reason  # type: ignore[attr-defined]


def test_there_is_no_repair_loop_for_classification() -> None:
    llm = FakeLLM(["bad", '{"source_type": "news"}'])
    result = classify(llm)
    assert len(llm.prompts) == 1
    assert result.method == "fallback"  # type: ignore[attr-defined]


def test_llm_failure_falls_back() -> None:
    err = LLMCallFailed("x", attempts=4, last_error=ProviderRateLimited("429"))
    result = classify(FakeLLM([err]))
    assert result.method == "fallback" and result.source_type == "other"  # type: ignore[attr-defined]


def test_cost_cap_exceeded_propagates() -> None:
    with pytest.raises(CostCapExceeded):
        classify(FakeLLM([CostCapExceeded(cap_usd=1, spent_usd=1)]))


def test_no_model_configured_falls_back_without_error() -> None:
    result = classify(None)
    assert result.source_type == "other" and result.method == "fallback"  # type: ignore[attr-defined]


def test_no_page_text_means_no_model_call() -> None:
    llm = FakeLLM([])
    result = classify(llm, text=None)
    assert result.method == "fallback" and llm.prompts == []  # type: ignore[attr-defined]


# --- prompt ----------------------------------------------------------------------------------


def test_call_has_no_tools_and_page_text_is_in_a_data_block() -> None:
    llm = FakeLLM(['{"source_type": "news"}'])
    classify(llm, text="Hello page body.")
    assert llm.searches == [False]
    prompt = llm.prompts[0]
    assert "Hello page body." in prompt
    assert prompt.count("<<<DATA-") == 1 and prompt.count("<<<END-DATA-") == 1
    assert (
        prompt.index("<<<DATA-") < prompt.index("Hello page body.") < prompt.index("<<<END-DATA-")
    )
    assert "blog.example.org" in prompt  # the domain is passed too, inside the block


def test_page_text_is_truncated_to_the_budget() -> None:
    llm = FakeLLM(['{"source_type": "news"}'])
    classifier = SourceClassifier(llm, provider="fake", model="m", max_chars=100)
    run_async(
        lambda: classifier.classify(
            domain="x.example.org",
            page_text="a" * 100 + "TAILMARKER",
            brand=BRAND,
            aliases=[],
            competitors=[],
        )
    )
    assert "TAILMARKER" not in llm.prompts[0]


def test_prompt_version_files_exist() -> None:
    assert (PROMPTS_DIR / "classify_source_v1.txt").is_file()
