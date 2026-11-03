import { describe, expect, it } from "vitest";
import {
  COST_INCREASE_LIMIT,
  METRICS,
  buildComparison,
  comparabilityWarnings,
  formatMetric,
  formatSpread,
  injectionStatuses,
  latestPerVersion,
  trendSeries,
  type EvalRun,
} from "./evals";

const stats = (mean: number, stdev = 0, values = [mean]) => ({ mean, stdev, min: Math.min(...values), max: Math.max(...values), values });

let seq = 0;
function run(version: string, started: string, metrics: Record<string, ReturnType<typeof stats>>, over: Partial<EvalRun> = {}): EvalRun {
  return {
    id: `r${++seq}`,
    prompt_version: version,
    classifier_prompt_version: "v1",
    repeats: 3,
    extractor_model: "gem-1",
    started_at: started,
    counts: { answers: 18, pages: 12, injection: 8 },
    metrics,
    per_repeat: [],
    notes: [],
    ...over,
  };
}
const full = (o: Record<string, number>) =>
  Object.fromEntries(
    Object.entries({
      extraction_accuracy: 0.8,
      false_mention_rate: 0.1,
      flag_rate: 0.2,
      source_check_accuracy: 0.9,
      injection_pass_rate: 1,
      total_cost_usd: 1,
      latency_p50_ms: 1000,
      latency_p90_ms: 2000,
      ...o,
    }).map(([k, v]) => [k, stats(v)]),
  );

describe("METRICS", () => {
  it("has the direction the backend uses: higher for accuracy and injection, lower for the rest", () => {
    const dir = Object.fromEntries(METRICS.map((m) => [m.key, m.direction]));
    expect(dir).toEqual({
      extraction_accuracy: "higher",
      false_mention_rate: "lower",
      flag_rate: "lower",
      source_check_accuracy: "higher",
      injection_pass_rate: "higher",
      total_cost_usd: "lower",
      latency_p50_ms: "lower",
      latency_p90_ms: "lower",
    });
  });
  it("uses the spec's 25% cost limit", () => {
    expect(COST_INCREASE_LIMIT).toBe(0.25);
  });
});

describe("latestPerVersion", () => {
  it("keeps the newest run of each prompt version, oldest version first", () => {
    const a1 = run("v1", "2026-03-01T00:00:00Z", full({}));
    const a2 = run("v1", "2026-03-05T00:00:00Z", full({}));
    const b = run("v2", "2026-03-03T00:00:00Z", full({}));
    const r = latestPerVersion([a2, b, a1]);
    expect(r.map((x) => x.id)).toEqual([a2.id, b.id]);
    expect(r.map((x) => x.prompt_version)).toEqual(["v1", "v2"]);
  });
});

describe("buildComparison winners", () => {
  const v1 = run("v1", "2026-03-01T00:00:00Z", full({}));
  const v2 = run("v2", "2026-03-02T00:00:00Z", full({ extraction_accuracy: 0.9, false_mention_rate: 0.05, flag_rate: 0.3, total_cost_usd: 1.1, latency_p50_ms: 900 }));

  it("picks winners by direction", () => {
    const c = buildComparison([v1, v2], "v1");
    const winner = (k: string) => c.rows.find((r) => r.metric.key === k)!.winner;
    expect(winner("extraction_accuracy")).toBe("v2"); // higher
    expect(winner("false_mention_rate")).toBe("v2"); // lower
    expect(winner("flag_rate")).toBe("v1"); // lower
    expect(winner("latency_p50_ms")).toBe("v2"); // lower
    expect(winner("total_cost_usd")).toBe("v1"); // lower
  });

  it("no winner on a tie, or when only one version has the metric", () => {
    const c = buildComparison([v1, v2], "v1");
    expect(c.rows.find((r) => r.metric.key === "injection_pass_rate")!.winner).toBeNull();
    const lone = buildComparison([v1, run("v2", "2026-03-02T00:00:00Z", { extraction_accuracy: stats(0.9) })], "v1");
    expect(lone.rows.find((r) => r.metric.key === "extraction_accuracy")!.winner).toBe("v2");
    expect(lone.rows.find((r) => r.metric.key === "flag_rate")!.winner).toBeNull();
  });

  it("with a single version there is no winner and no comparison", () => {
    const c = buildComparison([v1], "v1");
    expect(c.canCompare).toBe(false);
    expect(c.rows.every((r) => r.winner === null)).toBe(true);
  });

  it("warns when a winner costs more than 25% over the baseline", () => {
    const costly = run("v2", "2026-03-02T00:00:00Z", full({ extraction_accuracy: 0.95, total_cost_usd: 1.3 }));
    const c = buildComparison([v1, costly], "v1");
    expect(c.costWarnings).toEqual([{ version: "v2", increase: expect.closeTo(0.3, 5), metrics: ["extraction_accuracy"] }]);
  });

  it("does not warn at exactly 25%, or when the costly version wins nothing", () => {
    const edge = run("v2", "2026-03-02T00:00:00Z", full({ extraction_accuracy: 0.95, total_cost_usd: 1.25 }));
    expect(buildComparison([v1, edge], "v1").costWarnings).toEqual([]);
    const loser = run("v2", "2026-03-02T00:00:00Z", full({ extraction_accuracy: 0.5, flag_rate: 0.9, false_mention_rate: 0.5, source_check_accuracy: 0.1, total_cost_usd: 2, latency_p50_ms: 9000, latency_p90_ms: 9000 }));
    expect(buildComparison([v1, loser], "v1").costWarnings).toEqual([]);
  });

  it("the baseline never triggers a cost warning, and a missing cost gives no warning", () => {
    const c = buildComparison([run("v1", "2026-03-01T00:00:00Z", full({ total_cost_usd: 5 })), run("v2", "2026-03-02T00:00:00Z", full({ total_cost_usd: 1 }))], "v1");
    expect(c.costWarnings).toEqual([]);
    const noCost = buildComparison([v1, run("v2", "2026-03-02T00:00:00Z", { extraction_accuracy: stats(0.99) })], "v1");
    expect(noCost.costWarnings).toEqual([]);
  });

  it("deltas against the baseline say whether they are better or worse", () => {
    const c = buildComparison([v1, v2], "v1");
    const acc = c.rows.find((r) => r.metric.key === "extraction_accuracy")!.cells.find((x) => x.version === "v2")!;
    expect(acc.delta).toEqual({ text: "+10.0 pp", better: true });
    const cost = c.rows.find((r) => r.metric.key === "total_cost_usd")!.cells.find((x) => x.version === "v2")!;
    expect(cost.delta).toEqual({ text: "+10%", better: false });
    const base = c.rows.find((r) => r.metric.key === "total_cost_usd")!.cells.find((x) => x.version === "v1")!;
    expect(base.delta).toBeNull();
  });

  it("falls back to the earliest version when the chosen baseline does not exist", () => {
    expect(buildComparison([v1, v2], "nope").baseline).toBe("v1");
  });
});

describe("injectionStatuses", () => {
  it("is perfect only at 100%", () => {
    const s = injectionStatuses([run("v1", "2026-03-01T00:00:00Z", full({}))], [run("v1", "2026-03-01T00:00:00Z", full({}))]);
    expect(s[0]).toMatchObject({ version: "v1", status: "perfect" });
  });

  it("is below when under 100%", () => {
    const r = run("v1", "2026-03-01T00:00:00Z", full({ injection_pass_rate: 0.875 }));
    expect(injectionStatuses([r], [r])[0]).toMatchObject({ status: "below", rate: 0.875 });
  });

  it("is dropped when the same version got worse than its previous run", () => {
    const older = run("v1", "2026-03-01T00:00:00Z", full({ injection_pass_rate: 1 }));
    const newer = run("v1", "2026-03-05T00:00:00Z", full({ injection_pass_rate: 0.75 }));
    const s = injectionStatuses(latestPerVersion([older, newer]), [older, newer])[0]!;
    expect(s).toMatchObject({ status: "dropped", rate: 0.75, previous: 1 });
  });

  it("is missing when the run has no injection result", () => {
    const r = run("v1", "2026-03-01T00:00:00Z", { extraction_accuracy: stats(0.9) });
    expect(injectionStatuses([r], [r])[0]).toMatchObject({ status: "missing", rate: null });
  });
});

describe("trendSeries", () => {
  it("one time-ordered series per version, with the repeat spread", () => {
    const runs = [
      run("v1", "2026-03-05T00:00:00Z", { extraction_accuracy: stats(0.7, 0.1, [0.6, 0.8]) }),
      run("v1", "2026-03-01T00:00:00Z", { extraction_accuracy: stats(0.5) }),
      run("v2", "2026-03-03T00:00:00Z", { extraction_accuracy: stats(0.9) }),
      run("v2", "2026-03-04T00:00:00Z", { flag_rate: stats(0.1) }), // no accuracy: skipped
    ];
    const s = trendSeries(runs, "extraction_accuracy");
    expect(s.map((x) => x.version)).toEqual(["v1", "v2"]);
    expect(s[0]!.points.map((p) => p.mean)).toEqual([0.5, 0.7]);
    expect(s[0]!.points[1]).toMatchObject({ min: 0.6, max: 0.8 });
    expect(s[1]!.points).toHaveLength(1);
  });
});

describe("formatting", () => {
  it("formats by kind", () => {
    expect(formatMetric("rate", 0.8333)).toBe("83.3%");
    expect(formatMetric("usd", 0.0123)).toBe("$0.0123");
    expect(formatMetric("ms", 1234.4)).toBe("1,234 ms");
  });

  it("mean and spread, or a note when there is a single repeat", () => {
    expect(formatSpread("rate", stats(0.8, 0.02, [0.78, 0.8, 0.82]))).toEqual({ main: "80.0% ± 2.0 pp", range: "78.0% to 82.0%" });
    expect(formatSpread("rate", stats(0.8))).toEqual({ main: "80.0%", range: null });
    expect(formatSpread("usd", stats(1, 0.05, [0.95, 1.05]))).toEqual({ main: "$1.0000 ± 0.05", range: "$0.9500 to $1.0500" });
    expect(formatSpread("ms", stats(1000, 50, [950, 1050]))).toEqual({ main: "1,000 ms ± 50 ms", range: "950 ms to 1,050 ms" });
  });
});

describe("comparabilityWarnings", () => {
  it("flags differing dataset sizes, models and classifier prompts", () => {
    const a = run("v1", "2026-03-01T00:00:00Z", full({}));
    const b = run("v2", "2026-03-02T00:00:00Z", full({}), { counts: { answers: 20, pages: 12, injection: 8 }, extractor_model: "gem-2", classifier_prompt_version: "v2" });
    const w = comparabilityWarnings([a, b]);
    expect(w).toHaveLength(3);
    expect(w.join(" ")).toMatch(/answers/);
    expect(w.join(" ")).toMatch(/model/);
    expect(w.join(" ")).toMatch(/classifier/);
  });
  it("nothing to say when the runs match", () => {
    expect(comparabilityWarnings([run("v1", "2026-03-01T00:00:00Z", full({})), run("v2", "2026-03-02T00:00:00Z", full({}))])).toEqual([]);
  });
});
