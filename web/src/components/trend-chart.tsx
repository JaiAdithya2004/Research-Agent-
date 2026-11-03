import { buildChart, type Marker as MarkerShape } from "@/lib/chart";
import { formatMetric, type MetricDef, type TrendSeries } from "@/lib/evals";
import { formatDateTime, formatShortDate } from "@/lib/format";

function MarkerShapeSvg({ shape, x, y, color }: { shape: MarkerShape; x: number; y: number; color: string }) {
  const r = 4;
  const common = { fill: color, stroke: "var(--background)", strokeWidth: 1 };
  switch (shape) {
    case "square":
      return <rect x={x - r} y={y - r} width={r * 2} height={r * 2} {...common} />;
    case "triangle":
      return <polygon points={`${x},${y - r - 1} ${x + r + 1},${y + r} ${x - r - 1},${y + r}`} {...common} />;
    case "diamond":
      return <polygon points={`${x},${y - r - 1} ${x + r + 1},${y} ${x},${y + r + 1} ${x - r - 1},${y}`} {...common} />;
    default:
      return <circle cx={x} cy={y} r={r} {...common} />;
  }
}

/**
 * A small hand-built SVG trend of one metric across stored eval runs: one line per prompt version
 * (mean), with a vertical bar for the min to max across that run's repeats. Series differ by colour,
 * marker shape and line dash. The same numbers are available as a table for screen readers.
 */
export function TrendChart({ metric, series }: { metric: MetricDef; series: readonly TrendSeries[] }) {
  const chart = buildChart(series, metric.kind);
  const titleId = `trend-${metric.key}-title`;
  const descId = `trend-${metric.key}-desc`;
  const better = metric.direction === "higher" ? "higher is better" : "lower is better";
  const total = series.reduce((n, s) => n + s.points.length, 0);

  return (
    <figure className="rounded-lg border p-3" data-testid={`trend-${metric.key}`}>
      <figcaption className="mb-1 flex flex-wrap items-baseline justify-between gap-x-2">
        <span className="text-sm font-semibold">{metric.label}</span>
        <span className="text-xs text-muted-foreground">{better}</span>
      </figcaption>
      {chart.empty ? (
        <p className="py-6 text-center text-sm text-muted-foreground">No stored run has this metric yet.</p>
      ) : (
        <>
          <svg viewBox="0 0 320 140" role="img" aria-labelledby={titleId} aria-describedby={descId} className="h-auto w-full">
            <title id={titleId}>{`${metric.label} over time`}</title>
            <desc id={descId}>
              {series
                .map((s) => `${s.version}: ${s.points.map((p) => formatMetric(metric.kind, p.mean)).join(", then ")}`)
                .join(". ")}
              . {better}.
            </desc>
            {chart.yTicks.map((t) => (
              <g key={t.value}>
                <line x1={chart.plot.left} x2={chart.plot.right} y1={t.y} y2={t.y} stroke="var(--border)" strokeWidth={1} />
                <text x={chart.plot.left - 4} y={t.y + 3} textAnchor="end" fontSize={9} fill="var(--muted-foreground)">
                  {formatMetric(metric.kind, t.value).replace(/\.0%$/, "%")}
                </text>
              </g>
            ))}
            {chart.xTicks.map((t, i) => (
              <text
                key={t.t}
                x={t.x}
                y={chart.plot.bottom + 14}
                textAnchor={chart.xTicks.length === 1 ? "middle" : i === 0 ? "start" : "end"}
                fontSize={9}
                fill="var(--muted-foreground)"
              >
                {formatShortDate(t.t)}
              </text>
            ))}
            {chart.series.map((s) => (
              <g key={s.version} data-series={s.version}>
                {s.points.length > 1 ? (
                  <path d={s.path} fill="none" stroke={s.style.colorVar} strokeWidth={2} strokeDasharray={s.style.dash || undefined} />
                ) : null}
                {s.points.map((p) => (
                  <g key={p.runId}>
                    {p.yMin - p.yMax > 0.5 ? (
                      <line x1={p.x} x2={p.x} y1={p.yMin} y2={p.yMax} stroke={s.style.colorVar} strokeWidth={1.5} opacity={0.6} />
                    ) : null}
                    <MarkerShapeSvg shape={s.style.marker} x={p.x} y={p.y} color={s.style.colorVar} />
                  </g>
                ))}
              </g>
            ))}
          </svg>
          <ul className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs" aria-label={`Legend for ${metric.label}`}>
            {chart.series.map((s) => (
              <li key={s.version} className="flex items-center gap-1">
                <svg width="26" height="10" aria-hidden="true">
                  <line x1="0" x2="26" y1="5" y2="5" stroke={s.style.colorVar} strokeWidth={2} strokeDasharray={s.style.dash || undefined} />
                  <MarkerShapeSvg shape={s.style.marker} x={13} y={5} color={s.style.colorVar} />
                </svg>
                {s.version}
              </li>
            ))}
          </ul>
          <details className="mt-1 text-xs">
            <summary className="cursor-pointer text-muted-foreground">Show the numbers ({total} runs)</summary>
            <table className="mt-1 w-full">
              <caption className="sr-only">{metric.label} for every stored run</caption>
              <thead>
                <tr className="text-left">
                  <th scope="col" className="pr-2 font-medium">Version</th>
                  <th scope="col" className="pr-2 font-medium">Run started</th>
                  <th scope="col" className="pr-2 text-right font-medium">Mean</th>
                  <th scope="col" className="text-right font-medium">Range</th>
                </tr>
              </thead>
              <tbody>
                {series.flatMap((s) =>
                  s.points.map((p) => (
                    <tr key={p.runId}>
                      <th scope="row" className="pr-2 text-left font-normal">{s.version}</th>
                      <td className="pr-2">{formatDateTime(new Date(p.t).toISOString())}</td>
                      <td className="pr-2 text-right tabular-nums">{formatMetric(metric.kind, p.mean)}</td>
                      <td className="text-right tabular-nums">
                        {p.repeats > 1 ? `${formatMetric(metric.kind, p.min)} to ${formatMetric(metric.kind, p.max)}` : "1 repeat"}
                      </td>
                    </tr>
                  )),
                )}
              </tbody>
            </table>
          </details>
        </>
      )}
    </figure>
  );
}
