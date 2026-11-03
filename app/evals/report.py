"""Markdown output for eval results, ready to paste into the README."""

from app.evals.models import METRICS, EvalResult, MetricStats

COST_INCREASE_LIMIT = 0.25  # SPEC criterion 22: v2 must not raise cost by more than 25%


def _fmt(kind: str, value: float) -> str:
    if kind == "rate":
        return f"{value * 100:.1f}%"
    if kind == "usd":
        return f"${value:.4f}"
    return f"{value:,.0f} ms"


def _cell(kind: str, stats: MetricStats) -> str:
    if len(stats.values) > 1:
        spread = stats.stdev * 100 if kind == "rate" else stats.stdev
        unit = " pp" if kind == "rate" else ("" if kind == "usd" else " ms")
        sd = f"{spread:.2f}" if kind == "usd" else f"{spread:.1f}"
        return f"{_fmt(kind, stats.mean)} ± {sd}{unit}"
    return _fmt(kind, stats.mean)


def render_markdown(result: EvalResult) -> str:
    lines = [
        f"### Eval: prompt `{result.prompt_version}` "
        f"(classifier prompt `{result.classifier_prompt_version}`)",
        "",
        f"Model `{result.extractor_model}`, {result.repeats} repeats, "
        f"{result.counts['answers']} answers, {result.counts['pages']} pages, "
        f"{result.counts['injection']} injection pages. "
        + (
            "Mean ± standard deviation across repeats."
            if result.repeats > 1
            else "Single repeat, so no spread is shown."
        ),
        "",
        "| Metric | Result | Range across repeats |",
        "|---|---|---|",
    ]
    for name, (label, _direction, kind) in METRICS.items():
        stats = result.metrics.get(name)
        if stats is None:
            continue
        low, high = _fmt(kind, stats.min), _fmt(kind, stats.max)
        lines.append(f"| {label} | {_cell(kind, stats)} | {low} – {high} |")
    wrong = sorted({f for r in result.per_repeat for f in r.failures})
    if wrong:
        lines += ["", f"Items that failed in at least one repeat: {', '.join(wrong)}"]
    return "\n".join(lines) + "\n"


def winner(name: str, a: EvalResult, b: EvalResult) -> str | None:
    """The prompt version with the better mean for `name`, or None for a tie / missing data."""
    sa, sb = a.metrics.get(name), b.metrics.get(name)
    if sa is None or sb is None or sa.mean == sb.mean:
        return None
    higher_is_better = METRICS[name][1] == "higher"
    a_wins = (sa.mean > sb.mean) == higher_is_better
    return a.prompt_version if a_wins else b.prompt_version


def cost_increase(baseline: EvalResult, candidate: EvalResult) -> float | None:
    base = baseline.metrics.get("total_cost_usd")
    cand = candidate.metrics.get("total_cost_usd")
    if base is None or cand is None or base.mean <= 0:
        return None
    return cand.mean / base.mean - 1


def render_comparison(baseline: EvalResult, candidate: EvalResult) -> str:
    lines = [
        f"### Eval comparison: `{baseline.prompt_version}` (baseline) vs "
        f"`{candidate.prompt_version}` (candidate)",
        "",
        f"| Metric | {baseline.prompt_version} | {candidate.prompt_version} | Winner |",
        "|---|---|---|---|",
    ]
    for name, (label, _direction, kind) in METRICS.items():
        sa, sb = baseline.metrics.get(name), candidate.metrics.get(name)
        if sa is None or sb is None:
            continue
        w = winner(name, baseline, candidate)
        lines.append(
            f"| {label} | {_cell(kind, sa)} | {_cell(kind, sb)} | "
            f"{'**' + w + '**' if w else 'tie'} |"
        )
    increase = cost_increase(baseline, candidate)
    if increase is not None and increase > COST_INCREASE_LIMIT:
        lines += [
            "",
            f"> **Warning:** `{candidate.prompt_version}` costs {increase * 100:.0f}% more than "
            f"`{baseline.prompt_version}`, over the {COST_INCREASE_LIMIT * 100:.0f}% limit "
            "(SPEC criterion 22).",
        ]
    return "\n".join(lines) + "\n"
