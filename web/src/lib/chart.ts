import type { MetricKind, TrendSeries } from "@/lib/evals";

/**
 * Geometry for the small trend charts. Plain numbers in, plain numbers out, so the layout is tested
 * without a browser. The SVG itself is drawn by a component; no chart library is involved.
 */

export type Marker = "circle" | "square" | "triangle" | "diamond";

export interface SeriesStyle {
  marker: Marker;
  /** SVG stroke-dasharray; "" is a solid line. */
  dash: string;
  /** A CSS variable defined in globals.css (colour-blind-safe palette, light and dark). */
  colorVar: string;
}

/** Each series differs in colour AND marker shape AND line dash: colour is never the only cue. */
export const SERIES_STYLES: readonly SeriesStyle[] = [
  { marker: "circle", dash: "", colorVar: "var(--chart-1)" },
  { marker: "square", dash: "6 3", colorVar: "var(--chart-2)" },
  { marker: "triangle", dash: "2 3", colorVar: "var(--chart-3)" },
  { marker: "diamond", dash: "8 3 2 3", colorVar: "var(--chart-4)" },
];

export interface Layout {
  width: number;
  height: number;
  margin: { left: number; right: number; top: number; bottom: number };
}

export const DEFAULT_LAYOUT: Layout = { width: 320, height: 140, margin: { left: 46, right: 12, top: 12, bottom: 24 } };

export interface ChartPoint {
  x: number;
  y: number;
  yMin: number;
  yMax: number;
  mean: number;
  t: number;
  runId: string;
}

export interface Chart {
  empty: boolean;
  yTicks: Array<{ y: number; value: number }>;
  xTicks: Array<{ x: number; t: number }>;
  series: Array<{ version: string; style: SeriesStyle; path: string; points: ChartPoint[] }>;
  plot: { left: number; right: number; top: number; bottom: number };
}

export function buildChart(series: readonly TrendSeries[], kind: MetricKind, layout: Layout = DEFAULT_LAYOUT): Chart {
  const { width, height, margin } = layout;
  const plot = { left: margin.left, right: width - margin.right, top: margin.top, bottom: height - margin.bottom };
  const all = series.flatMap((s) => s.points);
  if (all.length === 0) return { empty: true, yTicks: [], xTicks: [], series: [], plot };

  let lo = Math.min(...all.map((p) => Math.min(p.min, p.mean)));
  let hi = Math.max(...all.map((p) => Math.max(p.max, p.mean)));
  if (hi - lo < 1e-12) {
    const pad = Math.abs(hi) * 0.1 || 0.05;
    lo -= pad;
    hi += pad;
  } else {
    const pad = (hi - lo) * 0.1;
    lo -= pad;
    hi += pad;
  }
  if (kind === "rate") {
    lo = Math.max(0, lo);
    hi = Math.min(1, hi);
  } else {
    lo = Math.max(0, lo);
  }
  if (hi <= lo) hi = lo + 0.05;

  const tMin = Math.min(...all.map((p) => p.t));
  const tMax = Math.max(...all.map((p) => p.t));
  const x = (t: number) => (tMax === tMin ? (plot.left + plot.right) / 2 : plot.left + ((t - tMin) / (tMax - tMin)) * (plot.right - plot.left));
  const y = (v: number) => plot.bottom - ((v - lo) / (hi - lo)) * (plot.bottom - plot.top);

  const built = series
    .filter((s) => s.points.length > 0)
    .map((s, i) => {
      const points = s.points.map((p) => ({ x: x(p.t), y: y(p.mean), yMin: y(p.min), yMax: y(p.max), mean: p.mean, t: p.t, runId: p.runId }));
      const path = points.map((p, j) => `${j === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
      return { version: s.version, style: SERIES_STYLES[i % SERIES_STYLES.length]!, path, points };
    });

  const yTicks = [lo, (lo + hi) / 2, hi].map((value) => ({ value, y: y(value) }));
  const xTicks = tMax === tMin ? [{ x: x(tMin), t: tMin }] : [{ x: x(tMin), t: tMin }, { x: x(tMax), t: tMax }];
  return { empty: false, yTicks, xTicks, series: built, plot };
}
