"""Summary metrics: pure functions over stored runs."""

from datetime import UTC, datetime
from typing import Any

import pytest

from app.audit.metrics import compute_summary, final_status, percentile, run_outcome
from app.audit.records import AuditState, ReviewState, RunRecord, StepKey
from app.schemas import Mention, Verdict
from app.verify.verifier import VerificationReport

NOW = datetime(2026, 11, 3, tzinfo=UTC)


def make_run(
    q: int = 0,
    repeat: int = 0,
    mode: str = "grounded",
    *,
    mentioned: bool = True,
    verified: bool = True,
    position: int | None = 1,
    latency: int = 100,
    status: str = "ok",
    provider: str = "gemini",
    model: str = "g",
    review: ReviewState | None = None,
    degraded: bool = False,
    competitors: list[str] | None = None,
    verification: VerificationReport | None = None,
) -> RunRecord:
    ok = status == "ok"
    return RunRecord(
        id=f"run-{q}-{repeat}-{mode}-{provider}",
        key=StepKey(
            question_idx=q,
            provider=provider,
            model=model,
            repeat_idx=repeat,
            search_mode=mode,  # type: ignore[arg-type]
        ),
        question=f"question {q}",
        status=status,  # type: ignore[arg-type]
        error=None if ok else "boom",
        raw_answer="answer" if ok else None,
        mention=Mention(
            brand_mentioned=mentioned,
            position=position if mentioned else None,
            quote="Acme" if mentioned else None,
            competitors_seen=competitors or [],
            sentiment="pos" if mentioned else "neu",
        )
        if ok
        else None,
        verdict=Verdict(status="verified" if verified else "flagged", reason="because")
        if ok
        else None,
        latency_ms=latency if ok else None,
        verification=verification,
        review=review,
        degraded=degraded,
    )


def state(**kw: Any) -> AuditState:
    data: dict[str, Any] = {
        "status": "complete",
        "total_cost_usd": 0.5,
        "search_calls": 4,
        "pages_fetched": 6,
    }
    data.update(kw)
    return AuditState(**data)


def summary(runs: list[RunRecord], mode: str = "grounded", **kw: Any):  # type: ignore[no-untyped-def]
    return compute_summary(runs, search_mode=mode, state=state(**kw))  # type: ignore[arg-type]


# --- outcome of one run -----------------------------------------------------------------------


def test_confirmed_mention_is_true() -> None:
    assert run_outcome(make_run(mentioned=True, verified=True)) is True


def test_unverified_mention_claim_is_not_counted_until_a_human_approves() -> None:
    assert run_outcome(make_run(mentioned=True, verified=False)) is False


def test_not_mentioned_is_false_even_when_flagged() -> None:
    assert run_outcome(make_run(mentioned=False, verified=False)) is False
    assert run_outcome(make_run(mentioned=False, verified=True)) is False


def test_failed_runs_have_no_outcome() -> None:
    assert run_outcome(make_run(status="failed")) is None


@pytest.mark.parametrize(
    ("review", "verified", "expected"),
    [
        (ReviewState(action="approve"), False, True),
        (ReviewState(action="reject"), True, False),
        (ReviewState(action="correct", corrected_mentioned=False), True, False),
        (ReviewState(action="correct", corrected_mentioned=True), False, True),
    ],
)
def test_human_review_overrides_the_automatic_outcome(
    review: ReviewState, verified: bool, expected: bool
) -> None:
    assert run_outcome(make_run(mentioned=True, verified=verified, review=review)) is expected


# --- rates -------------------------------------------------------------------------------------


def test_mention_rate_is_confirmed_mentions_over_usable_runs() -> None:
    runs = [
        make_run(0, 0),
        make_run(1, 0, mentioned=False),
        make_run(2, 0, mentioned=True, verified=False),
        make_run(3, 0, status="failed"),
    ]
    s = summary(runs)
    assert s.mention_rate == pytest.approx(
        1 / 3
    )  # failed run excluded; unverified claim not counted
    assert s.total_runs == 4 and s.failed_runs == 1


def test_avg_position_uses_only_confirmed_mentions_with_a_position() -> None:
    runs = [
        make_run(0, 0, position=1),
        make_run(1, 0, position=3),
        make_run(2, 0, mentioned=False),
        make_run(3, 0, position=2, verified=False),
    ]
    assert summary(runs).avg_position == pytest.approx(2.0)


def _report(computed: int | None) -> VerificationReport:
    return VerificationReport(
        verdict=Verdict(status="verified", reason="ok"),
        findings=[],
        prose_citations=[],
        computed_position=computed,
    )


def test_avg_position_ignores_a_position_the_code_could_not_verify() -> None:
    runs = [
        make_run(0, 0, position=2, verification=_report(2)),
        make_run(1, 0, position=5, verification=_report(None)),  # mention counts, position does not
    ]
    s = summary(runs)
    assert s.avg_position == pytest.approx(2.0)
    assert s.mention_rate == 1.0


def test_avg_position_is_none_when_never_mentioned() -> None:
    assert summary([make_run(0, 0, mentioned=False)]).avg_position is None


def test_no_runs_gives_zeroes_not_errors() -> None:
    s = summary([])
    assert (s.mention_rate, s.avg_position, s.total_runs, s.latency_p50_ms) == (0.0, None, 0, 0)


def test_flagged_count_counts_unresolved_flags_only() -> None:
    runs = [
        make_run(0, 0, verified=False),
        make_run(1, 0, verified=False, review=ReviewState(action="approve")),
        make_run(2, 0, verified=True),
    ]
    assert summary(runs).flagged_count == 1


def test_totals_come_from_the_audit_state() -> None:
    s = summary([make_run()], total_cost_usd=0.42, search_calls=7, pages_fetched=9)
    assert (s.total_cost_usd, s.search_calls, s.pages_fetched) == (0.42, 7, 9)


# --- stability: disagreement is never averaged away --------------------------------------------


def test_stability_is_one_when_repeats_agree() -> None:
    s = summary([make_run(0, r) for r in range(3)])
    assert s.stability == 1.0
    assert s.question_stability[0].outcomes == [True, True, True]
    assert s.question_stability[0].agreement == 1.0


def test_two_of_three_repeats_agreeing_is_two_thirds() -> None:
    runs = [make_run(0, 0), make_run(0, 1, mentioned=False), make_run(0, 2)]
    s = summary(runs)
    assert s.stability == pytest.approx(2 / 3)
    assert s.question_stability[0].outcomes == [True, False, True]


def test_a_fifty_fifty_split_scores_one_half() -> None:
    s = summary([make_run(0, 0), make_run(0, 1, mentioned=False)])
    assert s.stability == 0.5


def test_per_question_stability_keeps_the_unstable_question_visible() -> None:
    runs = [make_run(0, r) for r in range(2)] + [make_run(1, 0), make_run(1, 1, mentioned=False)]
    s = summary(runs)
    assert s.stability == pytest.approx(0.75)  # the mean...
    by_q = {q.question_idx: q.agreement for q in s.question_stability}
    assert by_q == {0: 1.0, 1: 0.5}  # ...but the disagreement is not hidden


def test_failed_repeats_are_ignored_in_agreement_but_shown_as_none() -> None:
    runs = [make_run(0, 0), make_run(0, 1, status="failed"), make_run(0, 2)]
    s = summary(runs)
    assert s.question_stability[0].outcomes == [True, None, True]
    assert s.question_stability[0].agreement == 1.0


def test_a_single_repeat_cannot_show_agreement() -> None:
    s = summary([make_run(0, 0), make_run(1, 0)])
    assert s.repeats_compared == 0
    assert all(q.agreement is None for q in s.question_stability)
    assert s.stability == 1.0  # nothing to disagree with, and repeats_compared says so


def test_stability_groups_are_per_model_and_mode() -> None:
    runs = [
        make_run(0, 0, provider="gemini"),
        make_run(0, 1, provider="gemini"),
        make_run(0, 0, provider="openai"),
        make_run(0, 1, provider="openai", mentioned=False),
    ]
    s = summary(runs)
    assert len(s.question_stability) == 2
    assert {q.provider: q.agreement for q in s.question_stability} == {"gemini": 1.0, "openai": 0.5}


# --- modes ---------------------------------------------------------------------------------------


def test_mode_delta_is_grounded_minus_off() -> None:
    runs = [
        make_run(0, 0, "grounded"),
        make_run(1, 0, "grounded"),
        make_run(0, 0, "off"),
        make_run(1, 0, "off", mentioned=False),
    ]
    s = summary(runs, "both")
    assert s.mode_delta == pytest.approx(0.5)
    assert s.mention_rate_by_mode == {"grounded": 1.0, "off": 0.5}
    assert s.mention_rate == 1.0  # headline is the grounded rate


def test_mode_delta_can_be_negative() -> None:
    runs = [make_run(0, 0, "grounded", mentioned=False), make_run(0, 0, "off")]
    assert summary(runs, "both").mode_delta == -1.0


def test_mode_delta_is_none_unless_both_modes_ran() -> None:
    assert summary([make_run(0, 0, "grounded")], "grounded").mode_delta is None
    assert summary([make_run(0, 0, "off")], "off").mode_delta is None
    assert summary([make_run(0, 0, "grounded")], "both").mode_delta is None  # no off data


def test_off_only_audit_reports_the_off_rate_as_headline() -> None:
    assert summary([make_run(0, 0, "off")], "off").mention_rate == 1.0


# --- latency ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("values", "p", "expected"),
    [
        ([], 50, 0),
        ([5], 90, 5),
        ([100, 200, 300, 400], 50, 200),
        (list(range(1, 11)), 90, 9),
        (list(range(1, 11)), 100, 10),
        ([3, 1, 2], 50, 2),
    ],
)
def test_percentile_nearest_rank(values: list[int], p: int, expected: int) -> None:
    assert percentile(values, p) == expected


def test_latency_percentiles_use_successful_runs() -> None:
    runs = [make_run(i, 0, latency=100 * (i + 1)) for i in range(10)] + [
        make_run(11, 0, status="failed")
    ]
    s = summary(runs)
    assert s.latency_p50_ms == 500 and s.latency_p90_ms == 900


# --- status ------------------------------------------------------------------------------------------


def test_degraded_and_caps_come_from_state_and_runs() -> None:
    s = summary([make_run()], degraded=True, caps_hit=["fetch_cap"], status="capped")
    assert s.degraded is True and s.status == "capped" and s.caps_hit == ["fetch_cap"]


def test_final_status_rules() -> None:
    ok, bad = make_run(), make_run(1, status="failed")
    assert final_status([ok, bad], []) == "complete"  # partial failure is not a failed audit
    assert final_status([ok], ["cost_cap"]) == "capped"
    assert final_status([bad], []) == "failed"
    assert final_status([], []) == "failed"
    assert final_status([bad], ["cost_cap"]) == "failed"  # nothing usable beats "capped"
