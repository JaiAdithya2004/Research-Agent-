import { describe, expect, it } from "vitest";
import { buildChart, SERIES_STYLES } from "./chart";
import type { TrendSeries } from "./evals";

const pt = (t: number, mean: number, min = mean, max = mean) => ({ t, runId: `r${t}`, mean, min, max, repeats: 1 });
const W = { width: 300, height: 120, margin: { left: 40, right: 10, top: 10, bottom: 20 } };

describe("buildChart", () => {
  it("maps higher values to smaller y (up) and later times to larger x", () => {
    const s: TrendSeries[] = [{ version: "v1", points: [pt(1000, 0.5), pt(2000, 0.9)] }];
    const c = buildChart(s, "rate", W);
    const [a, b] = c.series[0]!.points;
    expect(b!.y).toBeLessThan(a!.y);
    expect(b!.x).toBeGreaterThan(a!.x);
    expect(a!.x).toBeGreaterThanOrEqual(W.margin.left);
    expect(b!.x).toBeLessThanOrEqual(W.width - W.margin.right);
  });

  it("keeps rates inside 0 to 1 and the points inside the plot area", () => {
    const s: TrendSeries[] = [{ version: "v1", points: [pt(1, 0.95, 0.9, 1), pt(2, 1, 1, 1)] }];
    const c = buildChart(s, "rate", W);
    const top = c.yTicks[c.yTicks.length - 1]!;
    expect(top.value).toBeLessThanOrEqual(1);
    for (const p of c.series[0]!.points) {
      expect(p.y).toBeGreaterThanOrEqual(W.margin.top);
      expect(p.y).toBeLessThanOrEqual(W.height - W.margin.bottom);
      expect(p.yMin).toBeLessThanOrEqual(W.height - W.margin.bottom + 0.001);
      expect(p.yMax).toBeGreaterThanOrEqual(W.margin.top - 0.001);
    }
  });

  it("a single point is centred and a flat series still gets a usable axis", () => {
    const c = buildChart([{ version: "v1", points: [pt(5, 0.5)] }], "rate", W);
    const p = c.series[0]!.points[0]!;
    expect(p.x).toBeCloseTo((W.margin.left + (W.width - W.margin.right)) / 2);
    expect(c.yTicks[0]!.value).toBeLessThan(c.yTicks[c.yTicks.length - 1]!.value);
  });

  it("gives each series its own marker shape and dash, so colour is never the only cue", () => {
    const s: TrendSeries[] = [0, 1, 2, 3].map((i) => ({ version: `v${i}`, points: [pt(1, 0.1 * i), pt(2, 0.2 * i)] }));
    const c = buildChart(s, "rate", W);
    expect(new Set(c.series.map((x) => x.style.marker)).size).toBe(4);
    expect(new Set(c.series.map((x) => x.style.dash)).size).toBe(4);
  });

  it("wraps styles for more series than styles", () => {
    const many: TrendSeries[] = Array.from({ length: SERIES_STYLES.length + 1 }, (_, i) => ({ version: `v${i}`, points: [pt(1, 1)] }));
    expect(buildChart(many, "usd", W).series.at(-1)!.style).toEqual(SERIES_STYLES[0]);
  });

  it("builds a path through the means in time order", () => {
    const c = buildChart([{ version: "v1", points: [pt(1, 1), pt(2, 2), pt(3, 3)] }], "usd", W);
    expect(c.series[0]!.path.startsWith("M")).toBe(true);
    expect(c.series[0]!.path.match(/L/g)).toHaveLength(2);
  });

  it("is empty when there are no points", () => {
    expect(buildChart([], "rate", W).empty).toBe(true);
  });
});
