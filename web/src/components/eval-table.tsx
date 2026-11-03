import { ArrowDown, ArrowUp, Minus, Trophy } from "lucide-react";
import { Alert } from "@/components/ui/alert";
import { COST_INCREASE_LIMIT, METRICS, formatSpread, type Comparison, type Delta, type EvalRun } from "@/lib/evals";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

function DeltaText({ delta }: { delta: Delta }) {
  if (delta.better === null) {
    return (
      <span className="inline-flex items-center gap-0.5 text-xs text-muted-foreground">
        <Minus aria-hidden="true" className="size-3" />
        {delta.text}
      </span>
    );
  }
  return (
    <span className={cn("inline-flex items-center gap-0.5 text-xs font-medium", delta.better ? "text-success" : "text-destructive")}>
      {delta.better ? <ArrowUp aria-hidden="true" className="size-3" /> : <ArrowDown aria-hidden="true" className="size-3" />}
      {delta.text} <span>({delta.better ? "better" : "worse"})</span>
    </span>
  );
}

export function CostWarnings({ comparison }: { comparison: Comparison }) {
  if (comparison.costWarnings.length === 0) return null;
  const label = (key: string) => METRICS.find((m) => m.key === key)?.label ?? key;
  return (
    <div className="mb-4 space-y-3">
      {comparison.costWarnings.map((w) => (
        <Alert key={w.version} tone="warning" title={`${w.version} wins but costs ${Math.round(w.increase * 100)}% more`} role="alert" data-testid="cost-warning">
          <p>
            {w.version} is best on {w.metrics.map(label).join(", ")}, but its cost is {Math.round(w.increase * 100)}% above
            the baseline {comparison.baseline}. The limit is {Math.round(COST_INCREASE_LIMIT * 100)}% (SPEC acceptance
            criterion 22, the same limit the backend&apos;s compare command uses).
          </p>
        </Alert>
      ))}
    </div>
  );
}

export function EvalTable({ latest, comparison }: { latest: readonly EvalRun[]; comparison: Comparison }) {
  return (
    <div role="region" aria-label="Eval comparison table, scrolls sideways on small screens" tabIndex={0} className="overflow-x-auto rounded-lg border">
      <table className="w-full min-w-[40rem] text-sm">
        <caption className="sr-only">
          Prompt versions compared on each metric. Mean across repeats, with the spread. The best version per metric is marked.
        </caption>
        <thead className="bg-muted text-left">
          <tr>
            <th scope="col" className="px-3 py-2 font-semibold">Metric</th>
            {latest.map((r) => (
              <th key={r.id} scope="col" className="px-3 py-2 align-top font-semibold">
                <span className="block">
                  {r.prompt_version}
                  {comparison.canCompare && r.prompt_version === comparison.baseline ? (
                    <span className="ml-1 rounded border px-1 text-xs font-medium">baseline</span>
                  ) : null}
                </span>
                <span className="block text-xs font-normal text-muted-foreground">
                  {r.extractor_model}, {r.repeats} {r.repeats === 1 ? "repeat" : "repeats"}
                </span>
                <span className="block text-xs font-normal text-muted-foreground">
                  <time dateTime={r.started_at}>{formatDateTime(r.started_at)}</time>
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y">
          {comparison.rows.map((row) => (
            <tr key={row.metric.key} className={row.metric.key === "injection_pass_rate" ? "bg-muted/40" : undefined}>
              <th scope="row" className="px-3 py-2 text-left align-top font-medium">
                {row.metric.label}
                <span className="block text-xs font-normal text-muted-foreground">
                  {row.metric.direction === "higher" ? "Higher is better" : "Lower is better"}
                </span>
                {row.metric.caution ? <span className="block max-w-56 text-xs font-normal text-muted-foreground">{row.metric.caution}</span> : null}
              </th>
              {row.cells.map((cell) => {
                const spread = cell.stats ? formatSpread(row.metric.kind, cell.stats) : null;
                return (
                  <td
                    key={cell.version}
                    data-winner={cell.isWinner ? "true" : undefined}
                    className={cn("px-3 py-2 align-top", cell.isWinner && "border-x-2 border-success/70 bg-success-bg")}
                  >
                    {spread ? (
                      <>
                        <span className="block font-semibold tabular-nums">{spread.main}</span>
                        {spread.range ? <span className="block text-xs text-muted-foreground tabular-nums">range {spread.range}</span> : null}
                        {cell.isWinner ? (
                          <span className="mt-0.5 inline-flex items-center gap-1 text-xs font-semibold text-success">
                            <Trophy aria-hidden="true" className="size-3.5" />
                            Best
                          </span>
                        ) : null}
                        {cell.delta ? (
                          <span className="block">
                            <DeltaText delta={cell.delta} />
                          </span>
                        ) : null}
                      </>
                    ) : (
                      <span className="text-muted-foreground">
                        <span aria-hidden="true">—</span>
                        <span className="sr-only">Not measured in this run</span>
                      </span>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
