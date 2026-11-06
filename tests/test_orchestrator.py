"""The orchestrator: question x model x search mode x repeat -> answer, extract, verify, research.

Everything is faked (model client, fetcher); the real extractor, verifier, research loop and
metrics run on top of the fakes.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.audit.events import InMemoryEvents
from app.audit.records import StepKey
from app.audit.repo import InMemoryAuditRepo
from app.audit.runner import AuditConfig, AuditDeps, AuditOutcome, ModelTarget, run_audit
from app.llm.cost import CostTracker
from app.llm.types import LLMCallFailed, LLMResult, ProviderRateLimited
from app.research.classifier import SourceClassifier
from app.research.researcher import ResearchCaps
from app.schemas import AuditRequest, Citation
from tests.conftest import run_async
from tests.test_researcher import FakeFetcher
from tests.test_safe_fetch import T0

AUDIT_ID = "audit-1"
QUESTIONS = ["Best CRM for startups?", "Best project tool?"]
MARKER = re.compile(r"<<<ANSWER-[0-9a-f]{8}>>>\n(.*)\n<<<END-ANSWER-[0-9a-f]{8}>>>", re.DOTALL)


def cite(url: str) -> Citation:
    host = url.split("//", 1)[-1].split("/", 1)[0]
    return Citation(url=url, domain=host, title=None, source="grounding_metadata", retrieved_at=T0)


@dataclass
class Answer:
    text: str
    urls: list[str] = field(default_factory=list)
    degraded: bool = False


def default_answers(question: str, provider: str, search: bool, n: int) -> Answer:
    """Acme is mentioned for the first question only."""
    if question == QUESTIONS[0]:
        return Answer(
            "Top picks: Acme is a great CRM for startups. Rival is also popular.",
            ["https://reviews.example/crm"] if search else [],
        )
    return Answer(
        "Rival and Globex dominate project tools.", ["https://news.example/pm"] if search else []
    )


class FakeClient:
    """Stands in for LLMClient: same generate() signature, shared cost tracker, scripted answers."""

    def __init__(
        self,
        tracker: CostTracker,
        *,
        answers: Callable[[str, str, bool, int], Answer] = default_answers,
        cost: float = 0.0,
        down: set[str] | None = None,
        extraction_garbage: bool = False,
        explode_on: str | None = None,
    ) -> None:
        self.tracker = tracker
        self.answers = answers
        self.cost = cost
        self.down = down or set()
        self.extraction_garbage = extraction_garbage
        self.explode_on = explode_on
        self.calls: list[tuple[str, str, bool]] = []  # (kind, provider, search)
        self.backends: list[str] = []  # search_backend of every answer call
        self._counts: dict[tuple[str, str, bool], int] = {}

    def count(self, kind: str, provider: str | None = None) -> int:
        return sum(1 for k, p, _ in self.calls if k == kind and provider in (None, p))

    async def generate(
        self,
        *,
        provider: str,
        model: str,
        prompt: str,
        search: bool = False,
        search_backend: str = "provider",
    ) -> LLMResult:
        self.tracker.ensure_headroom()
        kind = (
            "extract"
            if "You extract how a brand" in prompt
            else "classify"
            if "You classify what kind" in prompt
            else "answer"
        )
        self.calls.append((kind, provider, search))
        if kind == "answer":
            self.backends.append(search_backend)
            if provider in self.down:
                raise LLMCallFailed(
                    "provider down", attempts=4, last_error=ProviderRateLimited("429")
                )
            if self.explode_on and self.explode_on in prompt:
                raise RuntimeError("unexpected bug")
            n = self._counts.get((prompt, provider, search), 0)
            self._counts[(prompt, provider, search)] = n + 1
            answer = self.answers(prompt, provider, search, n)
            text, citations, degraded = answer.text, [cite(u) for u in answer.urls], answer.degraded
            calls = 1 if (search and not degraded) else 0
        elif kind == "extract":
            text, citations, degraded, calls = self._extraction(prompt), [], False, 0
        else:
            text, citations, degraded, calls = '{"source_type": "other"}', [], False, 0
        self.tracker.record(cost_usd=self.cost, search_calls=calls)
        return LLMResult(
            provider=provider,
            model=model,
            text=text,
            citations=citations,
            input_tokens=10,
            output_tokens=5,
            search_calls=calls,
            latency_ms=120,
            cost_usd=self.cost,
            degraded=degraded,
            raw_response={"fake": True, "text": text},
        )

    def _extraction(self, prompt: str) -> str:
        if self.extraction_garbage:
            return "this is not json"
        match = MARKER.search(prompt)
        assert match, "extraction prompt without a data block"
        answer = match.group(1)
        mentioned = "Acme" in answer
        quote = next((s.strip() for s in re.split(r"(?<=[.!?])\s+", answer) if "Acme" in s), None)
        seen = [c for c in ("Rival", "Globex") if c in answer]
        return json.dumps(
            {
                "brand_mentioned": mentioned,
                "position": 1 if mentioned else None,
                "quote": quote if mentioned else None,
                "competitors_seen": seen,
                "sentiment": "pos" if mentioned else "neu",
            }
        )


def request(**kw: Any) -> AuditRequest:
    data: dict[str, Any] = {
        "brand": "Acme",
        "category": "crm",
        "competitors": ["Rival", "Globex"],
        "questions": QUESTIONS,
        "repeats": 2,
        "search_mode": "grounded",
        "research_depth": "cited_pages",
    }
    data.update(kw)
    return AuditRequest(**data)


@dataclass
class Harness:
    client: FakeClient
    fetcher: FakeFetcher
    repo: InMemoryAuditRepo
    events: InMemoryEvents
    tracker: CostTracker
    config: AuditConfig

    def run(self, req: AuditRequest) -> AuditOutcome:
        deps = AuditDeps(
            llm=self.client,
            tracker=self.tracker,
            fetcher=self.fetcher,
            classifier=SourceClassifier(self.client, provider="gemini", model="g"),
            repo=self.repo,
            events=self.events,
            config=self.config,
            now=lambda: T0,
        )
        return run_async(lambda: run_audit(AUDIT_ID, req, deps))


def harness(
    *,
    cap: float = 100.0,
    targets: list[ModelTarget] | None = None,
    repo: InMemoryAuditRepo | None = None,
    fetcher: FakeFetcher | None = None,
    config_kw: dict[str, Any] | None = None,
    **client_kw: Any,
) -> Harness:
    tracker = CostTracker(cap_usd=cap)
    config = AuditConfig(
        targets=targets or [ModelTarget("gemini", "g")],
        extractor=ModelTarget("gemini", "g"),
        **(config_kw or {}),
    )
    return Harness(
        client=FakeClient(tracker, **client_kw),
        fetcher=fetcher or FakeFetcher(),
        repo=repo or InMemoryAuditRepo(),
        events=InMemoryEvents(),
        tracker=tracker,
        config=config,
    )


TWO_PROVIDERS = [ModelTarget("gemini", "g"), ModelTarget("openai", "o")]


# --- the happy path ---------------------------------------------------------------------------------


def test_every_step_is_run_stored_and_summarised() -> None:
    h = harness()
    outcome = h.run(request())
    runs = h.repo.runs_of(AUDIT_ID)
    assert len(runs) == 4  # 2 questions x 2 repeats x 1 model x 1 mode
    assert all(r.status == "ok" for r in runs)
    s = outcome.summary
    assert outcome.status == "complete" and s.status == "complete"
    assert s.mention_rate == 0.5  # Acme in question 0 only
    assert s.avg_position == 1.0
    assert s.stability == 1.0
    assert s.total_runs == 4 and s.failed_runs == 0 and s.flagged_count == 0
    assert s.latency_p50_ms == 120


def test_runs_are_asked_like_a_user_with_the_question_as_the_prompt() -> None:
    h = harness()
    h.run(request())
    answer_prompts = {r.question for r in h.repo.runs_of(AUDIT_ID)}
    assert answer_prompts == set(QUESTIONS)


def test_raw_answer_and_grounding_are_stored_verbatim_and_never_overwritten() -> None:
    h = harness()
    h.run(request(repeats=1))
    run = h.repo.runs_of(AUDIT_ID)[0]
    assert run.raw_answer == "Top picks: Acme is a great CRM for startups. Rival is also popular."
    assert run.raw_grounding == {"fake": True, "text": run.raw_answer}
    tampered = run.model_copy(update={"raw_answer": "tampered"})
    with pytest.raises(ValueError, match="write-once"):
        run_async(lambda: h.repo.save_run(AUDIT_ID, tampered))
    assert h.repo.runs_of(AUDIT_ID)[0].raw_answer == run.raw_answer


def test_mentions_are_verified_and_citations_come_from_grounding_metadata() -> None:
    h = harness()
    h.run(request(repeats=1))
    run = next(r for r in h.repo.runs_of(AUDIT_ID) if r.key.question_idx == 0)
    assert run.verdict is not None and run.verdict.status == "verified"
    assert run.mention is not None and run.mention.brand_mentioned
    assert [c.url for c in run.citations] == ["https://reviews.example/crm"]
    assert all(c.source == "grounding_metadata" for c in run.citations)


def test_source_evidence_is_researched_once_per_url_across_runs() -> None:
    h = harness()
    outcome = h.run(request())
    assert sorted(h.fetcher.calls) == ["https://news.example/pm", "https://reviews.example/crm"]
    assert outcome.summary.pages_fetched == 2
    assert {p.evidence.url for p in h.repo.evidence_of(AUDIT_ID)} == set(h.fetcher.calls)


def test_research_depth_none_fetches_nothing() -> None:
    h = harness()
    outcome = h.run(request(research_depth="none"))
    assert h.fetcher.calls == [] and outcome.summary.pages_fetched == 0
    assert h.repo.evidence_of(AUDIT_ID) == []


def test_event_order_for_a_single_step() -> None:
    h = harness()
    h.run(request(questions=[QUESTIONS[0]], repeats=1))
    types = [e.type for e in h.events.events]
    assert types == [
        "audit_started",
        "run_started",
        "answer_extracted",
        "run_verified",
        "source_fetched",
        "audit_completed",
    ]
    assert all(e.audit_id == AUDIT_ID for e in h.events.events)


def test_events_never_carry_raw_answers_or_page_text() -> None:
    h = harness()
    h.run(request(repeats=1))
    blob = str([e.data for e in h.events.events])
    assert "Top picks" not in blob and "neutral page" not in blob


# --- partial failure: one provider down -----------------------------------------------------------------


def test_one_provider_down_does_not_kill_the_audit() -> None:
    h = harness(targets=TWO_PROVIDERS, down={"openai"})
    outcome = h.run(request())
    runs = h.repo.runs_of(AUDIT_ID)
    ok = [r for r in runs if r.status == "ok"]
    failed = [r for r in runs if r.status == "failed"]
    assert len(runs) == 8 and len(ok) == 4 and len(failed) == 4
    assert {r.key.provider for r in ok} == {"gemini"}
    assert outcome.status == "complete"  # partial failure is not a failed audit
    assert outcome.summary.failed_runs == 4
    assert outcome.summary.mention_rate == 0.5  # computed from the runs that worked
    assert all("down" in (r.error or "").lower() or r.error for r in failed)


def test_a_provider_that_keeps_failing_is_skipped_after_the_threshold() -> None:
    h = harness(targets=TWO_PROVIDERS, down={"openai"}, config_kw={"circuit_threshold": 2})
    h.run(request(repeats=3))
    # 6 openai steps planned, but only 2 were actually attempted before the breaker opened.
    assert h.client.count("answer", "openai") == 2
    skipped = [r for r in h.repo.runs_of(AUDIT_ID) if r.key.provider == "openai"]
    assert len(skipped) == 6 and all(r.status == "failed" for r in skipped)
    assert any("skipped" in (r.error or "") for r in skipped)


def test_a_success_resets_the_failure_streak() -> None:
    flaky_calls = {"n": 0}

    def answers(q: str, provider: str, search: bool, n: int) -> Answer:
        return default_answers(q, provider, search, n)

    h = harness(targets=TWO_PROVIDERS, answers=answers, config_kw={"circuit_threshold": 2})
    original = h.client.generate

    async def flaky(**kw: Any) -> LLMResult:
        if (
            kw["provider"] == "openai"
            and "You extract" not in kw["prompt"]
            and "You classify" not in kw["prompt"]
        ):
            flaky_calls["n"] += 1
            if flaky_calls["n"] % 2 == 1:  # fail, succeed, fail, succeed ...
                raise LLMCallFailed("blip", attempts=4, last_error=ProviderRateLimited("429"))
        return await original(**kw)

    h.client.generate = flaky  # type: ignore[method-assign]
    h.run(request(repeats=2))
    openai_runs = [r for r in h.repo.runs_of(AUDIT_ID) if r.key.provider == "openai"]
    assert (
        sum(r.status == "ok" for r in openai_runs) == 2
    )  # never tripped: no two failures in a row


def test_all_providers_down_means_a_failed_audit() -> None:
    h = harness(targets=TWO_PROVIDERS, down={"gemini", "openai"})
    outcome = h.run(request())
    assert outcome.status == "failed" and outcome.summary.status == "failed"
    assert h.events.events[-1].type == "audit_failed"


def test_extraction_failure_marks_the_run_failed_but_keeps_the_raw_answer() -> None:
    h = harness(extraction_garbage=True)
    outcome = h.run(request(repeats=1))
    runs = h.repo.runs_of(AUDIT_ID)
    assert all(r.status == "failed" and r.raw_answer for r in runs)
    assert all("extraction" in (r.error or "") for r in runs)
    assert outcome.status == "failed"


def test_an_unexpected_bug_in_one_step_is_contained() -> None:
    h = harness(explode_on=QUESTIONS[1])
    outcome = h.run(request(repeats=1))
    by_q = {r.key.question_idx: r for r in h.repo.runs_of(AUDIT_ID)}
    assert by_q[0].status == "ok" and by_q[1].status == "failed"
    assert "internal" in (by_q[1].error or "")
    assert outcome.status == "complete"


# --- caps: partial results, status capped ------------------------------------------------------------------


def test_cost_cap_stops_the_audit_and_returns_partial_results() -> None:
    h = harness(cap=0.75, cost=0.30)
    outcome = h.run(request(repeats=3))
    runs = h.repo.runs_of(AUDIT_ID)
    assert 0 < len(runs) < 6  # some work done, the rest never started
    assert outcome.status == "capped" and "cost_cap" in outcome.summary.caps_hit
    assert outcome.summary.total_runs == len(runs)
    assert h.events.events[-1].type == "audit_capped"
    assert outcome.summary.total_cost_usd == pytest.approx(h.tracker.spent_usd)


def test_a_cost_cap_does_not_mark_finished_runs_as_failed() -> None:
    h = harness(cap=0.75, cost=0.30)
    h.run(request(repeats=3))
    assert all(r.status == "ok" for r in h.repo.runs_of(AUDIT_ID))


def test_fetch_cap_gives_partial_evidence_and_status_capped() -> None:
    def answers(q: str, provider: str, search: bool, n: int) -> Answer:
        return Answer("Acme is great here.", [f"https://site{i}.example/p" for i in range(5)])

    h = harness(
        answers=answers, config_kw={"research_caps": ResearchCaps(max_fetches=2, max_per_domain=5)}
    )
    outcome = h.run(request(questions=[QUESTIONS[0]], repeats=1))
    assert len(h.fetcher.calls) == 2 and outcome.summary.pages_fetched == 2
    assert outcome.status == "capped" and "fetch_cap" in outcome.summary.caps_hit
    assert len(h.repo.runs_of(AUDIT_ID)) == 1  # the run itself is complete


def test_fetch_budget_is_shared_across_runs_not_reset_per_run() -> None:
    def answers(q: str, provider: str, search: bool, n: int) -> Answer:
        return Answer(
            "Acme is great here.", [f"https://site{q[:2]}{n}-{i}.example/p" for i in range(4)]
        )

    h = harness(
        answers=answers, config_kw={"research_caps": ResearchCaps(max_fetches=5, max_per_domain=5)}
    )
    outcome = h.run(request(repeats=2))
    assert len(h.fetcher.calls) == 5  # 4 runs x 4 URLs offered, 5 allowed in total
    assert outcome.status == "capped"


def test_max_steps_guard_never_runs_more_steps_than_allowed() -> None:
    h = harness(config_kw={"max_steps": 3})
    outcome = h.run(request(repeats=3))  # 6 steps planned
    assert len(h.repo.runs_of(AUDIT_ID)) == 3
    assert outcome.status == "capped" and "max_steps" in outcome.summary.caps_hit


def test_planned_steps_equal_questions_models_modes_and_repeats() -> None:
    h = harness(targets=TWO_PROVIDERS)
    h.run(request(search_mode="both", repeats=2))
    assert len(h.repo.runs_of(AUDIT_ID)) == 2 * 2 * 2 * 2  # q x repeats x models x modes


def test_repeats_are_interleaved_so_a_capped_audit_still_covers_every_question() -> None:
    h = harness(cap=0.75, cost=0.15)
    h.run(request(repeats=5))
    covered = {r.key.question_idx for r in h.repo.runs_of(AUDIT_ID)}
    assert covered == {0, 1}


# --- degraded -----------------------------------------------------------------------------------------------------


def test_search_fallback_marks_the_audit_degraded_but_complete() -> None:
    def answers(q: str, provider: str, search: bool, n: int) -> Answer:
        base = default_answers(q, provider, search, n)
        return Answer(base.text, [], degraded=True)  # search tool unavailable: no citations

    h = harness(answers=answers)
    outcome = h.run(request())
    assert outcome.status == "complete"
    assert outcome.summary.degraded is True
    assert all(r.degraded for r in h.repo.runs_of(AUDIT_ID))
    assert h.fetcher.calls == []  # no citations, nothing to research


def test_a_run_is_degraded_only_when_its_own_search_failed() -> None:
    def answers(q: str, provider: str, search: bool, n: int) -> Answer:
        base = default_answers(q, provider, search, n)
        return Answer(base.text, base.urls, degraded=(q == QUESTIONS[1]))

    h = harness(answers=answers)
    h.run(request(repeats=1))
    degraded = {r.key.question_idx: r.degraded for r in h.repo.runs_of(AUDIT_ID)}
    assert degraded == {0: False, 1: True}


# --- both modes -----------------------------------------------------------------------------------------------------


def both_mode_answers(question: str, provider: str, search: bool, n: int) -> Answer:
    if search:
        return Answer(
            "Acme is the best option here. Rival is cheaper.", ["https://reviews.example/crm"]
        )
    return Answer("Rival and Globex are the usual picks.", [])


def test_both_mode_runs_each_question_with_and_without_search_and_computes_the_delta() -> None:
    h = harness(answers=both_mode_answers)
    outcome = h.run(request(search_mode="both", repeats=2))
    runs = h.repo.runs_of(AUDIT_ID)
    assert {r.key.search_mode for r in runs} == {"off", "grounded"}
    s = outcome.summary
    assert s.mention_rate_by_mode == {"grounded": 1.0, "off": 0.0}
    assert s.mode_delta == 1.0
    assert s.mention_rate == 1.0  # headline = grounded
    off_runs = [r for r in runs if r.key.search_mode == "off"]
    assert all(r.search_calls == 0 and not r.citations for r in off_runs)


def test_tavily_mode_asks_for_the_tavily_backend_and_reports_its_own_mode() -> None:
    h = harness(answers=both_mode_answers)
    outcome = h.run(request(search_mode="tavily", repeats=1))
    runs = h.repo.runs_of(AUDIT_ID)
    assert {r.key.search_mode for r in runs} == {"tavily"}
    assert h.client.backends == ["tavily", "tavily"]  # one answer call per question
    assert [s for kind, _, s in h.client.calls if kind == "answer"] == [True, True]
    s = outcome.summary
    assert s.mention_rate == 1.0 and s.mention_rate_by_mode == {"tavily": 1.0}
    assert s.mode_delta is None
    assert all(r.search_calls == 1 and r.citations for r in runs)
    assert {row.search_mode for row in s.question_stability} == {"tavily"}


def test_provider_modes_never_ask_for_the_tavily_backend() -> None:
    h = harness(answers=both_mode_answers)
    h.run(request(search_mode="both", repeats=1))
    assert set(h.client.backends) == {"provider"}


def test_search_flag_is_passed_to_the_model_per_mode() -> None:
    h = harness(answers=both_mode_answers)
    h.run(request(search_mode="both", repeats=1))
    flags = [s for kind, _, s in h.client.calls if kind == "answer"]
    assert sorted(flags) == [False, False, True, True]


def test_mode_delta_is_none_for_single_mode_audits() -> None:
    assert harness().run(request()).summary.mode_delta is None
    assert harness().run(request(search_mode="off")).summary.mode_delta is None


# --- disagreement is visible ---------------------------------------------------------------------------------------


def test_repeats_that_disagree_are_reported_not_averaged_away() -> None:
    def answers(q: str, provider: str, search: bool, n: int) -> Answer:
        if n == 1:  # the second repeat misses the brand
            return Answer("Rival and Globex only.", [])
        return Answer("Acme is the top choice. Rival too.", [])

    h = harness(answers=answers)
    outcome = h.run(request(questions=[QUESTIONS[0]], repeats=3))
    s = outcome.summary
    assert s.question_stability[0].outcomes == [True, False, True]
    assert s.question_stability[0].agreement == pytest.approx(2 / 3)
    assert s.stability == pytest.approx(2 / 3)
    assert s.mention_rate == pytest.approx(2 / 3)


def test_a_flagged_mention_is_not_counted_until_reviewed() -> None:
    h = harness()
    original = h.client._extraction

    def bad_quote(prompt: str) -> str:
        return original(prompt).replace('"Top picks: Acme', '"Totally invented Acme')

    h.client._extraction = bad_quote  # type: ignore[method-assign]
    outcome = h.run(request(questions=[QUESTIONS[0]], repeats=1))
    run = h.repo.runs_of(AUDIT_ID)[0]
    assert run.verdict is not None and run.verdict.status == "flagged"
    assert "does not appear verbatim" in run.verdict.reason
    assert outcome.summary.flagged_count == 1
    assert outcome.summary.mention_rate == 0.0


def test_prose_only_urls_are_stored_as_unverified_citations() -> None:
    def answers(q: str, provider: str, search: bool, n: int) -> Answer:
        return Answer("Acme is great, see https://blog.example/post for more.", [])

    h = harness(answers=answers)
    h.run(request(questions=[QUESTIONS[0]], repeats=1))
    run = h.repo.runs_of(AUDIT_ID)[0]
    prose = [c for c in run.citations if c.source == "answer_text"]
    assert [c.url for c in prose] == ["https://blog.example/post"]
    assert run.verdict is not None and run.verdict.status == "flagged"
    assert h.fetcher.calls == []  # unverified URLs are never fetched


# --- idempotency and resume -----------------------------------------------------------------------------------------


def test_rerunning_a_finished_audit_makes_no_new_model_search_or_fetch_calls() -> None:
    h = harness()
    first = h.run(request())
    calls, fetches, spent = len(h.client.calls), len(h.fetcher.calls), h.tracker.spent_usd
    h2 = harness(repo=h.repo)  # a "retried job": fresh process, same database
    second = h2.run(request())
    assert h2.client.calls == [] and h2.fetcher.calls == []
    assert (calls, fetches) == (len(h.client.calls), len(h.fetcher.calls))
    assert second.summary.model_dump() == first.summary.model_dump()
    assert len(h.repo.runs_of(AUDIT_ID)) == 4  # no duplicate rows
    assert spent == h.tracker.spent_usd


def test_a_capped_audit_can_resume_and_only_does_the_remaining_steps() -> None:
    h = harness(cap=0.75, cost=0.30)
    first = h.run(request(repeats=3))
    done = len(h.repo.runs_of(AUDIT_ID))
    assert first.status == "capped" and done < 6
    h2 = harness(cap=100.0, repo=h.repo)
    second = h2.run(request(repeats=3))
    assert len(h2.repo.runs_of(AUDIT_ID)) == 6 == len(h.repo.runs_of(AUDIT_ID))
    assert h2.client.count("answer") == 6 - done  # only the missing steps were asked
    assert second.status == "complete"


def test_resume_continues_cost_accounting_from_what_was_already_spent() -> None:
    h = harness(cap=0.75, cost=0.30)
    h.run(request(repeats=3))
    spent_before = h.repo.audit_state(AUDIT_ID).total_cost_usd
    h2 = harness(cap=0.75, cost=0.30, repo=h.repo)
    second = h2.run(request(repeats=3))
    assert h2.tracker.spent_usd >= spent_before  # the tracker started from the stored total
    assert second.status == "capped"
    assert len(h2.client.calls) == 0  # the cap was already reached: nothing new is spent


def test_step_keys_are_unique_per_question_model_mode_and_repeat() -> None:
    h = harness(targets=TWO_PROVIDERS)
    h.run(request(search_mode="both", repeats=2))
    keys = [r.key for r in h.repo.runs_of(AUDIT_ID)]
    assert len(keys) == len(set(keys)) == 16
    assert (
        StepKey(question_idx=0, provider="gemini", model="g", repeat_idx=0, search_mode="off")
        in keys
    )


def test_cost_cap_exceeded_is_never_leaked_to_the_caller() -> None:
    h = harness(cap=0.1, cost=0.30)
    outcome = h.run(request())  # must not raise CostCapExceeded
    assert outcome.status in {"capped", "failed"}


class Crash(BaseException):
    """A job dying mid-step (worker killed). Not an Exception, so nothing may swallow it."""


def test_a_crash_after_the_model_answered_does_not_pay_for_the_answer_twice() -> None:
    h = harness()
    original = h.client._extraction

    def die_once(prompt: str) -> str:
        h.client._extraction = original  # type: ignore[method-assign]
        raise Crash

    h.client._extraction = die_once  # type: ignore[method-assign]
    with pytest.raises(Crash):
        h.run(request(questions=[QUESTIONS[0]], repeats=1))
    pending = h.repo.runs_of(AUDIT_ID)
    assert len(pending) == 1 and pending[0].status == "pending" and pending[0].raw_answer
    answers_before = h.client.count("answer")

    h2 = harness(repo=h.repo)  # the retried job
    outcome = h2.run(request(questions=[QUESTIONS[0]], repeats=1))
    assert h2.client.count("answer") == 0  # the stored answer was reused
    assert answers_before == 1
    run = h.repo.runs_of(AUDIT_ID)[0]
    assert run.status == "ok" and run.mention is not None and outcome.status == "complete"


def test_degraded_is_persisted_on_the_audit_state_not_only_in_the_summary() -> None:
    def answers(q: str, provider: str, search: bool, n: int) -> Answer:
        return Answer("Rival only.", [], degraded=True)

    h = harness(answers=answers)
    h.run(request(repeats=1))
    assert h.repo.audit_state(AUDIT_ID).degraded is True


def test_failed_runs_are_not_researched() -> None:
    h = harness(extraction_garbage=True)
    h.run(request(repeats=1))
    assert h.fetcher.calls == []  # their citations are stored with the raw answer, not fetched


def test_a_failed_model_call_logs_why_without_the_raw_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    h = harness(targets=TWO_PROVIDERS, down={"openai"}, config_kw={"circuit_threshold": 2})
    with caplog.at_level("WARNING", logger="app.audit"):
        h.run(request(repeats=1))
    logged = [r for r in caplog.records if r.getMessage() == "run_model_call_failed"]
    assert logged, "a failed call must leave a log line"
    assert logged[0].reason == "ProviderRateLimited: 429"  # type: ignore[attr-defined]
    assert logged[0].attempts == 4  # type: ignore[attr-defined]
