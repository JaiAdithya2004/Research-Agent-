"""Audit summary metrics: pure functions over stored runs.

Definitions (see also the README):
  * A run is a *confirmed mention* when the extractor said the brand was mentioned AND the
    verifier verified it (or a human approved it). An unverified claim is not counted until a
    human approves it; `flagged_count` says how many are waiting.
  * mention_rate = confirmed mentions / usable runs, in the headline search mode (grounded when
    the audit ran grounded or both, otherwise off). Failed runs are excluded.
  * stability = share of repeats of the same (question, model, mode) that agree with their own
    majority outcome. It is reported per group and never averaged away: `question_stability`
    keeps each group's outcomes, and the headline is only the mean of those.
"""

import math
from collections import defaultdict
from collections.abc import Sequence

from app.audit.records import AuditState, RunRecord
from app.schemas import AuditStatus, AuditSummary, QuestionStability, SearchMode


def percentile(values: Sequence[int], p: int) -> int:
    """Nearest-rank percentile; 0 for no data."""
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def run_outcome(run: RunRecord) -> bool | None:
    """True: brand confirmed mentioned. False: not mentioned or not confirmed. None: no data."""
    if run.status != "ok" or run.mention is None:
        return None
    mentioned = run.mention.brand_mentioned
    review = run.review
    if review is not None:
        if review.action == "approve":
            return mentioned
        if review.action == "reject":
            return False
        return mentioned if review.corrected_mentioned is None else review.corrected_mentioned
    confirmed = run.verdict is not None and run.verdict.status == "verified"
    return mentioned and confirmed


def final_status(runs: Sequence[RunRecord], caps_hit: Sequence[str]) -> AuditStatus:
    if not any(r.status == "ok" for r in runs):
        return "failed"  # nothing usable beats "capped"
    return "capped" if caps_hit else "complete"


def _rate(outcomes: list[bool]) -> float:
    return sum(outcomes) / len(outcomes) if outcomes else 0.0


def compute_summary(
    runs: Sequence[RunRecord], *, search_mode: SearchMode, state: AuditState
) -> AuditSummary:
    by_mode: dict[str, list[bool]] = defaultdict(list)
    for run in runs:
        outcome = run_outcome(run)
        if outcome is not None:
            by_mode[run.key.search_mode].append(outcome)

    headline = "off" if search_mode == "off" else "grounded"
    if search_mode == "both" and "grounded" not in by_mode and "off" in by_mode:
        headline = "off"
    rates = {mode: _rate(outcomes) for mode, outcomes in by_mode.items()}
    mode_delta = (
        rates["grounded"] - rates["off"]
        if search_mode == "both" and "grounded" in rates and "off" in rates
        else None
    )

    positions = [
        run.mention.position
        for run in runs
        if run.key.search_mode == headline
        and run.mention is not None
        and run.mention.position is not None
        and run_outcome(run) is True
    ]

    groups: dict[tuple[int, str, str, str], list[RunRecord]] = defaultdict(list)
    for run in runs:
        k = run.key
        groups[(k.question_idx, k.provider, k.model, k.search_mode)].append(run)
    stability_rows: list[QuestionStability] = []
    for (q, provider, model, mode), group in sorted(groups.items()):
        ordered = sorted(group, key=lambda r: r.key.repeat_idx)
        outcomes = [run_outcome(r) for r in ordered]
        known = [o for o in outcomes if o is not None]
        agreement = (
            max(known.count(True), known.count(False)) / len(known) if len(known) >= 2 else None
        )
        stability_rows.append(
            QuestionStability(
                question_idx=q,
                provider=provider,
                model=model,
                search_mode=mode,  # type: ignore[arg-type]
                outcomes=outcomes,
                agreement=agreement,
            )
        )
    agreements = [row.agreement for row in stability_rows if row.agreement is not None]

    latencies = [r.latency_ms for r in runs if r.status == "ok" and r.latency_ms is not None]
    flagged = sum(
        1
        for r in runs
        if r.status == "ok"
        and r.verdict is not None
        and r.verdict.status == "flagged"
        and r.review is None
    )
    return AuditSummary(
        mention_rate=rates.get(headline, 0.0),
        avg_position=sum(positions) / len(positions) if positions else None,
        stability=sum(agreements) / len(agreements) if agreements else 1.0,
        flagged_count=flagged,
        total_cost_usd=state.total_cost_usd,
        search_calls=state.search_calls,
        pages_fetched=state.pages_fetched,
        latency_p50_ms=percentile(latencies, 50),
        latency_p90_ms=percentile(latencies, 90),
        status=state.status,
        degraded=state.degraded or any(r.degraded for r in runs),
        mode_delta=mode_delta,
        total_runs=len(runs),
        failed_runs=sum(1 for r in runs if r.status == "failed"),
        caps_hit=list(state.caps_hit),
        mention_rate_by_mode=rates,
        repeats_compared=len(agreements),
        question_stability=stability_rows,
    )
