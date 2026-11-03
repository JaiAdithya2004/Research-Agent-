"""The eval suite: dataset loading, metric maths, repeats, prompt versions, reports and the CLI.

The model is faked; the extractor, verifier, safe fetcher, page check and classifier are real.
"""

import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.evals.__main__ import main
from app.evals.datasets import DatasetError, load_answers, load_injection, load_pages
from app.evals.models import EvalResult, MetricStats, RepeatResult
from app.evals.report import cost_increase, render_comparison, render_markdown, winner
from app.evals.runner import classifier_version, run_eval
from app.extract.prompts import PROMPTS_DIR
from app.llm.types import LLMResult
from tests.conftest import run_async
from tests.test_extractor import llm_result

SAMPLE = Path(__file__).resolve().parents[1] / "evals" / "sample"
MARK = re.compile(r"<<<ANSWER-[0-9a-f]{8}>>>\n(.*)\n<<<END-ANSWER-[0-9a-f]{8}>>>", re.DOTALL)


class EvalFakeLLM:
    """A stand-in model. `overrides` maps a substring of the answer to a forced extraction reply;
    `vulnerable` makes it change its classification when the page text contains an injection."""

    def __init__(
        self,
        *,
        overrides: dict[str, str] | None = None,
        vulnerable: bool = False,
        cost: float = 0.01,
        latency: int = 100,
    ) -> None:
        self.overrides = overrides or {}
        self.vulnerable = vulnerable
        self.cost = cost
        self.latency = latency
        self.prompts: list[str] = []

    async def generate(
        self, *, provider: str, model: str, prompt: str, search: bool = False
    ) -> LLMResult:
        self.prompts.append(prompt)
        if "You extract how a brand" in prompt:
            text = self._extract(prompt)
        elif "You classify what kind" in prompt:
            text = (
                '{"source_type": "news"}'
                if self.vulnerable and "Ignore previous" in prompt
                else '{"source_type": "other"}'
            )
        else:
            text = "{}"
        base = llm_result(text, cost=self.cost)
        return base.model_copy(update={"latency_ms": self.latency + len(self.prompts)})

    def _extract(self, prompt: str) -> str:
        answer = MARK.search(prompt).group(1)  # type: ignore[union-attr]
        for needle, reply in self.overrides.items():
            if needle in answer:
                return reply
        brand = re.search(r"^BRAND: (.*)$", prompt, re.MULTILINE).group(1).strip()  # type: ignore[union-attr]
        low = answer.lower()
        mentioned = brand.lower() in low and f"unlike {brand.lower()}" not in low
        quote = next(
            (s for s in re.split(r"(?<=[.!?])\s+", answer) if brand.lower() in s.lower()), None
        )
        competitors = re.search(r"^COMPETITORS: (.*)$", prompt, re.MULTILINE).group(1)  # type: ignore[union-attr]
        seen = [c.strip() for c in competitors.split(",") if c.strip() in answer]
        return json.dumps(
            {
                "brand_mentioned": mentioned,
                "position": 1 if mentioned else None,
                "quote": quote if mentioned else None,
                "competitors_seen": seen,
                "sentiment": "pos" if mentioned else "neu",
            }
        )


def run(**kw: Any) -> EvalResult:
    defaults: dict[str, Any] = {
        "llm_factory": lambda: EvalFakeLLM(),
        "provider": "fake",
        "model": "m",
        "prompt_version": "v1",
        "repeats": 1,
        "answers": load_answers(SAMPLE / "answers.json"),
        "pages": load_pages(SAMPLE / "pages.json"),
        "injection": load_injection(SAMPLE / "injection.json"),
    }
    defaults.update(kw)
    return run_async(lambda: run_eval(**defaults))


def mean(result: EvalResult, name: str) -> float:
    return result.metrics[name].mean


# --- dataset loading ------------------------------------------------------------------------------------


def test_the_sample_files_load() -> None:
    assert len(load_answers(SAMPLE / "answers.json")) == 4
    assert len(load_pages(SAMPLE / "pages.json")) == 5
    assert len(load_injection(SAMPLE / "injection.json")) == 2


def test_a_missing_file_says_where_to_look(tmp_path: Path) -> None:
    with pytest.raises(DatasetError, match=r"not found.*evals/README\.md"):
        load_answers(tmp_path / "answers.json")


@pytest.mark.parametrize("content", ["{not json", "[]", '{"a": 1}'])
def test_malformed_files_are_rejected(tmp_path: Path, content: str) -> None:
    path = tmp_path / "a.json"
    path.write_text(content, encoding="utf8")
    with pytest.raises(DatasetError):
        load_answers(path)


def test_schema_errors_name_the_item_and_the_field(tmp_path: Path) -> None:
    items = json.loads((SAMPLE / "answers.json").read_text(encoding="utf8"))
    del items[1]["expected"]
    path = tmp_path / "a.json"
    path.write_text(json.dumps(items), encoding="utf8")
    with pytest.raises(DatasetError, match=r"item #1.*expected"):
        load_answers(path)


def test_unknown_fields_and_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    items = json.loads((SAMPLE / "answers.json").read_text(encoding="utf8"))
    items[0]["surprise"] = 1
    path = tmp_path / "a.json"
    path.write_text(json.dumps(items), encoding="utf8")
    with pytest.raises(DatasetError, match="surprise|extra"):
        load_answers(path)
    items = json.loads((SAMPLE / "answers.json").read_text(encoding="utf8"))
    items[1]["id"] = items[0]["id"]
    path.write_text(json.dumps(items), encoding="utf8")
    with pytest.raises(DatasetError, match="duplicate id"):
        load_answers(path)


def test_a_page_label_cannot_claim_a_brand_verdict_for_an_unread_page(tmp_path: Path) -> None:
    items = json.loads((SAMPLE / "pages.json").read_text(encoding="utf8"))
    items[2]["expected"]["brand_on_page"] = False  # paywalled but labeled "absent"
    path = tmp_path / "p.json"
    path.write_text(json.dumps(items), encoding="utf8")
    # The label itself is allowed to be wrong (it is data); the eval then scores it as a miss.
    pages = load_pages(path)
    result = run(pages=pages, answers=None, injection=None)
    assert mean(result, "source_check_accuracy") == pytest.approx(4 / 5)


# --- metrics -------------------------------------------------------------------------------------------------


def test_a_perfect_run_scores_perfectly_and_the_foil_is_flagged() -> None:
    result = run()
    assert mean(result, "extraction_accuracy") == 1.0
    assert mean(result, "false_mention_rate") == 0.0
    assert mean(result, "flag_rate") == 0.25  # "Unlike Acme ..." is flagged for human review
    assert mean(result, "source_check_accuracy") == 1.0
    assert mean(result, "injection_pass_rate") == 1.0
    assert result.counts == {"answers": 4, "pages": 5, "injection": 2}


def test_a_wrong_extraction_lowers_accuracy_and_is_listed() -> None:
    wrong = json.dumps(
        {
            "brand_mentioned": False,
            "position": None,
            "quote": None,
            "competitors_seen": [],
            "sentiment": "neu",
        }
    )
    result = run(llm_factory=lambda: EvalFakeLLM(overrides={"best CRM for startups": wrong}))
    assert mean(result, "extraction_accuracy") == pytest.approx(3 / 4)
    assert "s-a1" in result.per_repeat[0].failures


def test_position_and_sentiment_are_scored_when_labeled() -> None:
    bad_position = json.dumps(
        {
            "brand_mentioned": True,
            "position": 3,
            "quote": "Acme is the best CRM for startups.",
            "competitors_seen": ["Rival"],
            "sentiment": "pos",
        }
    )
    bad_sentiment = json.dumps(
        {
            "brand_mentioned": True,
            "position": 1,
            "quote": "Acme is the best CRM for startups.",
            "competitors_seen": ["Rival"],
            "sentiment": "neg",
        }
    )
    for forced in (bad_position, bad_sentiment):
        result = run(
            llm_factory=lambda f=forced: EvalFakeLLM(overrides={"best CRM for startups": f})
        )
        assert mean(result, "extraction_accuracy") == pytest.approx(3 / 4)


def test_false_mention_rate_counts_only_answers_labeled_not_mentioned() -> None:
    forced = json.dumps(
        {
            "brand_mentioned": True,
            "position": 1,
            "quote": "Unlike Acme, Rival offers 24/7 support.",
            "competitors_seen": ["Rival"],
            "sentiment": "pos",
        }
    )
    result = run(llm_factory=lambda: EvalFakeLLM(overrides={"Unlike Acme": forced}))
    assert mean(result, "false_mention_rate") == pytest.approx(
        1 / 2
    )  # 2 negatives, 1 wrongly mentioned
    assert mean(result, "extraction_accuracy") == pytest.approx(3 / 4)


def test_a_failed_extraction_counts_as_wrong_and_does_not_crash_the_run() -> None:
    result = run(
        llm_factory=lambda: EvalFakeLLM(overrides={"Rival and Globex dominate": "garbage"})
    )
    assert mean(result, "extraction_accuracy") == pytest.approx(3 / 4)
    assert any("extraction failed" in f for f in result.per_repeat[0].failures)


def test_flag_rate_rises_when_a_quote_is_not_in_the_answer() -> None:
    invented = json.dumps(
        {
            "brand_mentioned": True,
            "position": 1,
            "quote": "Totally invented sentence about Acme.",
            "competitors_seen": ["Rival"],
            "sentiment": "pos",
        }
    )
    result = run(llm_factory=lambda: EvalFakeLLM(overrides={"best CRM for startups": invented}))
    assert mean(result, "flag_rate") == pytest.approx(2 / 4)


def test_page_checks_run_through_the_real_fetcher_without_a_model_for_the_verdict() -> None:
    result = run(answers=None, injection=None)
    assert mean(result, "source_check_accuracy") == 1.0
    assert result.per_repeat[0].failures == []


def test_a_mislabelled_page_is_reported_as_a_miss(tmp_path: Path) -> None:
    items = json.loads((SAMPLE / "pages.json").read_text(encoding="utf8"))
    items[0]["expected"]["brand_on_page"] = False
    path = tmp_path / "p.json"
    path.write_text(json.dumps(items), encoding="utf8")
    result = run(pages=load_pages(path), answers=None, injection=None)
    assert mean(result, "source_check_accuracy") == pytest.approx(4 / 5)
    assert result.per_repeat[0].failures == ["s-p1"]


def test_injection_passes_when_the_output_matches_the_clean_page() -> None:
    assert mean(run(answers=None, pages=None), "injection_pass_rate") == 1.0


def test_injection_fails_when_a_model_obeys_the_injected_text() -> None:
    result = run(llm_factory=lambda: EvalFakeLLM(vulnerable=True), answers=None, pages=None)
    assert mean(result, "injection_pass_rate") == pytest.approx(1 / 2)
    assert result.per_repeat[0].failures == [
        "s-i2 (say_brand_x_is_best)"
    ]  # visible instruction only


def test_hidden_injection_cannot_fool_even_a_vulnerable_model() -> None:
    result = run(
        llm_factory=lambda: EvalFakeLLM(vulnerable=True),
        answers=None,
        pages=None,
        injection=[
            i for i in load_injection(SAMPLE / "injection.json") if i.technique == "html_comment"
        ],
    )
    assert mean(result, "injection_pass_rate") == 1.0


def test_suites_are_optional() -> None:
    result = run(answers=None, pages=None, injection=None)
    assert set(result.metrics) == {"total_cost_usd", "latency_p50_ms", "latency_p90_ms"}


# --- repeats: mean and spread ---------------------------------------------------------------------------------


def test_repeats_produce_one_value_per_repeat() -> None:
    result = run(repeats=3)
    assert result.repeats == 3 and len(result.per_repeat) == 3
    assert all(len(s.values) == 3 for s in result.metrics.values())
    assert result.metrics["extraction_accuracy"].stdev == 0.0  # deterministic fake


def test_spread_is_reported_when_repeats_disagree() -> None:
    created: list[int] = []

    def flaky_factory() -> EvalFakeLLM:
        created.append(1)
        wrong = json.dumps(
            {
                "brand_mentioned": False,
                "position": None,
                "quote": None,
                "competitors_seen": [],
                "sentiment": "neu",
            }
        )
        # Repeat 2 (the second one) gets a model that misses the first answer.
        return EvalFakeLLM(overrides={"best CRM for startups": wrong} if len(created) == 2 else {})

    result = run(llm_factory=flaky_factory, repeats=3, pages=None, injection=None)
    stats = result.metrics["extraction_accuracy"]
    assert stats.values == [1.0, 0.75, 1.0]
    assert stats.min == 0.75 and stats.max == 1.0
    assert stats.mean == pytest.approx(11 / 12)
    assert stats.stdev == pytest.approx(0.1179, abs=1e-3)


def test_cost_and_latency_cover_every_model_call() -> None:
    result = run(llm_factory=lambda: EvalFakeLLM(cost=0.02, latency=100))
    calls = result.per_repeat[0].llm_calls
    assert calls > 0
    assert mean(result, "total_cost_usd") == pytest.approx(0.02 * calls)
    assert 101 <= mean(result, "latency_p50_ms") <= mean(result, "latency_p90_ms") <= 100 + calls


# --- prompt versions ----------------------------------------------------------------------------------------------


def make_prompts_dir(tmp_path: Path, version: str, *, classifier: bool) -> Path:
    for name in ("extract_v1.txt", "extract_repair_v1.txt", "classify_source_v1.txt"):
        shutil.copy(PROMPTS_DIR / name, tmp_path / name)
    marker = f"PROMPT-VERSION-MARKER-{version}\n"
    (tmp_path / f"extract_{version}.txt").write_text(
        marker + (PROMPTS_DIR / "extract_v1.txt").read_text(encoding="utf8"), encoding="utf8"
    )
    shutil.copy(PROMPTS_DIR / "extract_repair_v1.txt", tmp_path / f"extract_repair_{version}.txt")
    if classifier:
        (tmp_path / f"classify_source_{version}.txt").write_text(
            marker + (PROMPTS_DIR / "classify_source_v1.txt").read_text(encoding="utf8"),
            encoding="utf8",
        )
    return tmp_path


def test_the_prompt_version_argument_selects_the_prompt_files(tmp_path: Path) -> None:
    prompts = make_prompts_dir(tmp_path, "v2", classifier=False)
    llms: list[EvalFakeLLM] = []

    def factory() -> EvalFakeLLM:
        llms.append(EvalFakeLLM())
        return llms[-1]

    result = run(llm_factory=factory, prompt_version="v2", prompts_dir=prompts)
    assert result.prompt_version == "v2" and result.classifier_prompt_version == "v1"
    extraction = [p for p in llms[0].prompts if "You extract how a brand" in p]
    assert extraction and all("PROMPT-VERSION-MARKER-v2" in p for p in extraction)
    classify = [p for p in llms[0].prompts if "You classify what kind" in p]
    assert classify and all("PROMPT-VERSION-MARKER" not in p for p in classify)


def test_a_matching_classifier_prompt_is_used_when_it_exists(tmp_path: Path) -> None:
    prompts = make_prompts_dir(tmp_path, "v2", classifier=True)
    assert classifier_version("v2", prompts) == "v2"
    llms: list[EvalFakeLLM] = []

    def factory() -> EvalFakeLLM:
        llms.append(EvalFakeLLM())
        return llms[-1]

    run(
        llm_factory=factory,
        prompt_version="v2",
        prompts_dir=prompts,
        answers=None,
        injection=None,
    )
    classify = [p for p in llms[0].prompts if "You classify what kind" in p]
    assert classify and all("PROMPT-VERSION-MARKER-v2" in p for p in classify)


def test_an_unknown_prompt_version_fails_loudly() -> None:
    with pytest.raises(FileNotFoundError):
        run(prompt_version="v99")


def test_v1_and_v2_can_be_compared_on_the_same_data(tmp_path: Path) -> None:
    prompts = make_prompts_dir(tmp_path, "v2", classifier=False)
    a = run(prompts_dir=prompts)
    b = run(prompt_version="v2", prompts_dir=prompts)
    assert {a.prompt_version, b.prompt_version} == {"v1", "v2"}
    assert "Eval comparison" in render_comparison(a, b)


# --- markdown report -------------------------------------------------------------------------------------------------


def synthetic(version: str, **values: list[float]) -> EvalResult:
    def stats(vs: list[float]) -> MetricStats:
        m = sum(vs) / len(vs)
        sd = (sum((v - m) ** 2 for v in vs) / len(vs)) ** 0.5
        return MetricStats(values=vs, mean=m, stdev=sd, min=min(vs), max=max(vs))

    return EvalResult(
        prompt_version=version,
        classifier_prompt_version="v1",
        repeats=len(next(iter(values.values()))),
        extractor_model="gemini:m",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        counts={"answers": 18, "pages": 12, "injection": 8},
        metrics={k: stats(v) for k, v in values.items()},
        per_repeat=[
            RepeatResult(repeat=i, metrics={}, llm_calls=1, failures=["x1"] if i == 0 else [])
            for i in range(len(next(iter(values.values()))))
        ],
    )


def test_markdown_table_has_every_metric_with_mean_and_spread() -> None:
    md = render_markdown(
        synthetic(
            "v1",
            extraction_accuracy=[0.9, 0.95, 0.85],
            false_mention_rate=[0.1, 0.1, 0.1],
            total_cost_usd=[0.1, 0.12, 0.11],
            latency_p50_ms=[900, 1000, 800],
            latency_p90_ms=[2000, 2200, 1800],
        )
    )
    lines = md.splitlines()
    assert lines[0].startswith("### Eval: prompt `v1`")
    assert "| Metric | Result | Range across repeats |" in lines
    assert any(
        line.startswith("| Extraction accuracy | 90.0% ± 4.1 pp | 85.0% – 95.0% |")
        for line in lines
    )
    assert any(line.startswith("| False-mention rate | 10.0% ± 0.0 pp |") for line in lines)
    assert any("Total cost | $0.1100" in line for line in lines)
    assert any("Latency P50 | 900 ms ± 81.6 ms" in line for line in lines)
    assert "Items that failed in at least one repeat: x1" in md
    assert "Injection pass rate" not in md  # metrics that were not run are not shown


def test_single_repeat_shows_no_spread() -> None:
    md = render_markdown(synthetic("v1", extraction_accuracy=[0.5]))
    assert "| Extraction accuracy | 50.0% |" in md and "±" not in md


# --- comparison, winners, cost warning -------------------------------------------------------------------------------


def test_winner_respects_the_direction_of_each_metric() -> None:
    a = synthetic(
        "v1",
        extraction_accuracy=[0.8],
        false_mention_rate=[0.2],
        total_cost_usd=[0.1],
        injection_pass_rate=[1.0],
    )
    b = synthetic(
        "v2",
        extraction_accuracy=[0.9],
        false_mention_rate=[0.1],
        total_cost_usd=[0.2],
        injection_pass_rate=[1.0],
    )
    assert winner("extraction_accuracy", a, b) == "v2"  # higher is better
    assert winner("false_mention_rate", a, b) == "v2"  # lower is better
    assert winner("total_cost_usd", a, b) == "v1"  # lower is better
    assert winner("injection_pass_rate", a, b) is None  # tie
    assert winner("flag_rate", a, b) is None  # missing


@pytest.mark.parametrize(
    ("candidate_cost", "warns"), [(0.125, False), (0.1251, True), (0.2, True), (0.05, False)]
)
def test_cost_warning_triggers_only_above_the_25_percent_limit(
    candidate_cost: float, warns: bool
) -> None:
    base = synthetic("v1", total_cost_usd=[0.1])
    cand = synthetic("v2", total_cost_usd=[candidate_cost])
    assert ("Warning" in render_comparison(base, cand)) is warns
    assert cost_increase(base, cand) == pytest.approx(candidate_cost / 0.1 - 1)


def test_cost_increase_is_none_without_a_usable_baseline() -> None:
    assert (
        cost_increase(
            synthetic("v1", extraction_accuracy=[1.0]), synthetic("v2", total_cost_usd=[1.0])
        )
        is None
    )
    assert (
        cost_increase(synthetic("v1", total_cost_usd=[0.0]), synthetic("v2", total_cost_usd=[1.0]))
        is None
    )


# --- CLI -------------------------------------------------------------------------------------------------------------------


def test_cli_run_prints_markdown_writes_json_and_applies_the_cost_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, Any] = {}

    def fake_from_env(**kw: Any) -> EvalFakeLLM:
        seen.update(kw)
        return EvalFakeLLM()

    monkeypatch.setattr("app.evals.__main__.LLMClient.from_env", staticmethod(fake_from_env))
    out = tmp_path / "res" / "v1.json"
    code = main(
        [
            "run",
            "--prompt-version",
            "v1",
            "--repeats",
            "2",
            "--max-cost",
            "0.5",
            "--answers",
            str(SAMPLE / "answers.json"),
            "--pages",
            str(SAMPLE / "pages.json"),
            "--injection",
            str(SAMPLE / "injection.json"),
            "--out",
            str(out),
        ]
    )
    printed = capsys.readouterr().out
    assert code == 0
    assert "### Eval: prompt `v1`" in printed and "| Extraction accuracy |" in printed
    assert seen["cost_cap_usd"] == 0.5 and seen["tracker"].cap_usd == 0.5
    saved = EvalResult.model_validate_json(out.read_text(encoding="utf8"))
    assert saved.repeats == 2 and saved.prompt_version == "v1"


def test_cli_run_with_a_missing_dataset_exits_2_and_explains(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code = main(["run", "--prompt-version", "v1", "--answers", str(tmp_path / "nope.json")])
    assert code == 2 and "not found" in capsys.readouterr().err


def test_cli_run_with_an_unknown_prompt_version_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "run",
            "--prompt-version",
            "v99",
            "--answers",
            str(SAMPLE / "answers.json"),
            "--pages",
            str(SAMPLE / "pages.json"),
            "--injection",
            str(SAMPLE / "injection.json"),
        ]
    )
    assert code == 2 and "prompts/extract_v99.txt" in capsys.readouterr().err


def test_cli_compare_prints_the_comparison(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(synthetic("v1", total_cost_usd=[0.1]).model_dump_json(), encoding="utf8")
    b.write_text(synthetic("v2", total_cost_usd=[0.2]).model_dump_json(), encoding="utf8")
    assert main(["compare", str(a), str(b)]) == 0
    printed = capsys.readouterr().out
    assert "baseline" in printed and "Warning" in printed and "100%" in printed
