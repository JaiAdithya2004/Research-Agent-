/**
 * Eval comparison logic. Plain functions, no React.
 *
 * The API returns metrics keyed by name but says nothing about their meaning, direction or unit, so
 * those are mirrored here from the backend (`app/evals/models.py` METRICS) and the spec. Winners
 * are decided on the mean across repeats, in the metric's direction.
 */
import type { components } from "@/lib/api/schema.gen";

export type EvalRun = components["schemas"]["EvalRunOut"];
export type MetricStats = components["schemas"]["MetricStats"];

export type Direction = "higher" | "lower";
export type MetricKind = "rate" | "usd" | "ms";

export interface MetricDef {
  key: string;
  label: string;
  direction: Direction;
  kind: MetricKind;
  /** One sentence on what it measures (from evals/README.md). */
  help: string;
  /** Extra honesty about how to read it. */
  caution?: string;
}

/** SPEC acceptance criterion 22 and the backend's COST_INCREASE_LIMIT: no more than +25% cost. */
export const COST_INCREASE_LIMIT = 0.25;

export const METRICS: readonly MetricDef[] = [
  {
    key: "extraction_accuracy",
    label: "Extraction accuracy",
    direction: "higher",
    kind: "rate",
    help: "Share of labelled answers where the extracted brand_mentioned (and position and sentiment, when labelled) is right. A failed extraction counts as wrong.",
  },
  {
    key: "false_mention_rate",
    label: "False-mention rate",
    direction: "lower",
    kind: "rate",
    help: "Of the answers labelled not mentioned, the share where the extractor said the brand was mentioned.",
  },
  {
    key: "flag_rate",
    label: "Flag rate",
    direction: "lower",
    kind: "rate",
    help: "Share of extracted answers the deterministic verifier flagged for a human to look at.",
    caution:
      "Lower is treated as better because every flag costs a person's time. It is not an accuracy measure: a low flag rate with a high false-mention rate means flags are being missed.",
  },
  {
    key: "source_check_accuracy",
    label: "Source-check accuracy",
    direction: "higher",
    kind: "rate",
    help: "Share of labelled pages where the fetch status and brand-on-page result are right.",
  },
  {
    key: "injection_pass_rate",
    label: "Injection pass rate",
    direction: "higher",
    kind: "rate",
    help: "Share of prompt-injection pages where the output is identical to the clean page. Anything under 100% means a page could steer the model.",
  },
  {
    key: "total_cost_usd",
    label: "Cost",
    direction: "lower",
    kind: "usd",
    help: "Total model spend for one repeat of the whole eval suite.",
  },
  { key: "latency_p50_ms", label: "Latency P50", direction: "lower", kind: "ms", help: "Median latency of a model call." },
  { key: "latency_p90_ms", label: "Latency P90", direction: "lower", kind: "ms", help: "90th percentile latency of a model call." },
];

export const COST_KEY = "total_cost_usd";
export const INJECTION_KEY = "injection_pass_rate";

const EPS = 1e-9;

const time = (r: EvalRun) => Date.parse(r.started_at);

/** The newest run of each prompt version, ordered by when each version first appeared (oldest first). */
export function latestPerVersion(runs: readonly EvalRun[]): EvalRun[] {
  const latest = new Map<string, EvalRun>();
  const first = new Map<string, number>();
  for (const r of runs) {
    const cur = latest.get(r.prompt_version);
    if (!cur || time(r) > time(cur)) latest.set(r.prompt_version, r);
    first.set(r.prompt_version, Math.min(first.get(r.prompt_version) ?? Infinity, time(r)));
  }
  return [...latest.values()].sort((a, b) => first.get(a.prompt_version)! - first.get(b.prompt_version)!);
}

// ----------------------------------------------------------------------------- formatting

export function formatMetric(kind: MetricKind, value: number): string {
  if (kind === "rate") return `${(value * 100).toFixed(1)}%`;
  if (kind === "usd") return `$${value.toFixed(4)}`;
  return `${Math.round(value).toLocaleString("en-US")} ms`;
}

export function formatSpread(kind: MetricKind, s: MetricStats): { main: string; range: string | null } {
  const mean = formatMetric(kind, s.mean);
  if (s.values.length <= 1) return { main: mean, range: null };
  const spread =
    kind === "rate" ? `${(s.stdev * 100).toFixed(1)} pp` : kind === "usd" ? s.stdev.toFixed(2) : `${Math.round(s.stdev)} ms`;
  return { main: `${mean} ± ${spread}`, range: `${formatMetric(kind, s.min)} to ${formatMetric(kind, s.max)}` };
}

// ----------------------------------------------------------------------------- comparison

export interface Delta {
  text: string;
  /** null: no change. */
  better: boolean | null;
}

function deltaVs(def: MetricDef, value: number, base: number): Delta | null {
  const diff = value - base;
  let text: string;
  if (def.kind === "rate") {
    const pts = diff * 100;
    if (Math.abs(pts) < 0.05) return { text: "no change", better: null };
    text = `${pts > 0 ? "+" : "−"}${Math.abs(pts).toFixed(1)} pp`;
  } else {
    if (base === 0) return null;
    const pct = Math.round((diff / base) * 100);
    if (pct === 0) return { text: "no change", better: null };
    text = `${pct > 0 ? "+" : "−"}${Math.abs(pct)}%`;
  }
  const improved = def.direction === "higher" ? diff > 0 : diff < 0;
  return { text, better: improved };
}

export interface Cell {
  version: string;
  stats: MetricStats | null;
  isWinner: boolean;
  /** Against the baseline version; null for the baseline itself or when either side is missing. */
  delta: Delta | null;
}

export interface Row {
  metric: MetricDef;
  cells: Cell[];
  winner: string | null;
}

export interface CostWarning {
  version: string;
  /** 0.3 means 30% more than the baseline. */
  increase: number;
  /** Metrics (keys) this version wins. */
  metrics: string[];
}

export interface Comparison {
  versions: string[];
  baseline: string;
  canCompare: boolean;
  rows: Row[];
  costWarnings: CostWarning[];
}

export function buildComparison(latest: readonly EvalRun[], baselineVersion: string | null): Comparison {
  const versions = latest.map((r) => r.prompt_version);
  const baseline = baselineVersion !== null && versions.includes(baselineVersion) ? baselineVersion : (versions[0] ?? "");
  const canCompare = latest.length >= 2;
  const statsOf = (r: EvalRun, key: string): MetricStats | null => r.metrics[key] ?? null;
  const baseRun = latest.find((r) => r.prompt_version === baseline);

  const rows: Row[] = METRICS.map((metric) => {
    const present = latest.map((r) => ({ version: r.prompt_version, stats: statsOf(r, metric.key) }));
    let winner: string | null = null;
    const withData = present.filter((p): p is { version: string; stats: MetricStats } => p.stats !== null);
    if (canCompare && withData.length >= 2) {
      const sign = metric.direction === "higher" ? 1 : -1;
      const sorted = [...withData].sort((a, b) => sign * (b.stats.mean - a.stats.mean));
      const best = sorted[0]!;
      const tied = Math.abs(best.stats.mean - sorted[1]!.stats.mean) < EPS;
      winner = tied ? null : best.version;
    }
    const baseStats = baseRun ? statsOf(baseRun, metric.key) : null;
    const cells: Cell[] = present.map((p) => ({
      version: p.version,
      stats: p.stats,
      isWinner: winner === p.version,
      delta: canCompare && p.version !== baseline && p.stats && baseStats ? deltaVs(metric, p.stats.mean, baseStats.mean) : null,
    }));
    return { metric, cells, winner };
  });

  const costWarnings: CostWarning[] = [];
  const baseCost = baseRun ? statsOf(baseRun, COST_KEY) : null;
  if (canCompare && baseCost && baseCost.mean > 0) {
    for (const r of latest) {
      if (r.prompt_version === baseline) continue;
      const cost = statsOf(r, COST_KEY);
      if (!cost) continue;
      const increase = cost.mean / baseCost.mean - 1;
      const wins = rows.filter((row) => row.winner === r.prompt_version).map((row) => row.metric.key);
      if (increase > COST_INCREASE_LIMIT && wins.length > 0) costWarnings.push({ version: r.prompt_version, increase, metrics: wins });
    }
  }
  return { versions, baseline, canCompare, rows, costWarnings };
}

// ----------------------------------------------------------------------------- injection

export type InjectionState = "perfect" | "below" | "dropped" | "missing";

export interface InjectionStatus {
  version: string;
  runId: string;
  rate: number | null;
  /** The same version's previous run, when there is one with a result. */
  previous: number | null;
  status: InjectionState;
}

export function injectionStatuses(latest: readonly EvalRun[], all: readonly EvalRun[]): InjectionStatus[] {
  return latest.map((r) => {
    const rate = r.metrics[INJECTION_KEY]?.mean ?? null;
    const earlier = all
      .filter((o) => o.prompt_version === r.prompt_version && o.id !== r.id && time(o) < time(r) && o.metrics[INJECTION_KEY])
      .sort((a, b) => time(b) - time(a))[0];
    const previous = earlier?.metrics[INJECTION_KEY]?.mean ?? null;
    let status: InjectionState;
    if (rate === null) status = "missing";
    else if (previous !== null && rate < previous - EPS) status = "dropped";
    else if (rate < 1 - EPS) status = "below";
    else status = "perfect";
    return { version: r.prompt_version, runId: r.id, rate, previous, status };
  });
}

// ----------------------------------------------------------------------------- trends

export interface TrendPoint {
  t: number;
  runId: string;
  mean: number;
  min: number;
  max: number;
  repeats: number;
}

export interface TrendSeries {
  version: string;
  points: TrendPoint[];
}

/** One time-ordered series per prompt version for one metric. Runs without the metric are skipped. */
export function trendSeries(runs: readonly EvalRun[], key: string): TrendSeries[] {
  const byVersion = new Map<string, TrendPoint[]>();
  const first = new Map<string, number>();
  for (const r of runs) {
    first.set(r.prompt_version, Math.min(first.get(r.prompt_version) ?? Infinity, time(r)));
    const s = r.metrics[key];
    if (!s) continue;
    const list = byVersion.get(r.prompt_version) ?? [];
    list.push({ t: time(r), runId: r.id, mean: s.mean, min: s.min, max: s.max, repeats: s.values.length });
    byVersion.set(r.prompt_version, list);
  }
  return [...byVersion.entries()]
    .sort((a, b) => first.get(a[0])! - first.get(b[0])!)
    .map(([version, points]) => ({ version, points: points.sort((a, b) => a.t - b.t) }));
}

// ----------------------------------------------------------------------------- comparability

/** Reasons the numbers of the compared runs may not be like for like. Empty when they match. */
export function comparabilityWarnings(latest: readonly EvalRun[]): string[] {
  if (latest.length < 2) return [];
  const out: string[] = [];
  const countKeys = new Set(latest.flatMap((r) => Object.keys(r.counts)));
  for (const k of [...countKeys].sort()) {
    const values = latest.map((r) => r.counts[k]);
    if (new Set(values).size > 1) {
      out.push(`The runs used different numbers of ${k} items (${values.map((v) => v ?? "none").join(" vs ")}), so their accuracy and cost are not directly comparable.`);
    }
  }
  const models = [...new Set(latest.map((r) => r.extractor_model))];
  if (models.length > 1) out.push(`The runs used different extractor models (${models.join(", ")}), so a difference may come from the model, not the prompt.`);
  const classifiers = [...new Set(latest.map((r) => r.classifier_prompt_version))];
  if (classifiers.length > 1) out.push(`The runs used different classifier prompts (${classifiers.join(", ")}), which affects the source-check results.`);
  return out;
}
