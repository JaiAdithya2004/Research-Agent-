import { CheckCircle2, HelpCircle, ShieldAlert, TrendingDown } from "lucide-react";
import { Alert } from "@/components/ui/alert";
import { formatMetric, type InjectionStatus } from "@/lib/evals";
import { cn } from "@/lib/utils";

const STYLE = {
  perfect: { box: "border-success/60 bg-success-bg", text: "text-success", Icon: CheckCircle2, label: "All injection pages passed" },
  below: { box: "border-destructive border-2 bg-destructive-bg", text: "text-destructive", Icon: ShieldAlert, label: "Some injection pages changed the output" },
  dropped: { box: "border-destructive border-2 bg-destructive-bg", text: "text-destructive", Icon: TrendingDown, label: "Dropped since the previous run" },
  missing: { box: "border-dashed border-warning/70 bg-warning-bg", text: "text-warning", Icon: HelpCircle, label: "No injection result in this run" },
} as const;

/**
 * The injection pass rate gets its own prominent panel above the table. Anything but 100% is a
 * security regression, so it is a red alert with an icon and words, not just a number.
 */
export function InjectionPanel({ statuses }: { statuses: readonly InjectionStatus[] }) {
  const bad = statuses.filter((s) => s.status === "below" || s.status === "dropped");
  const missing = statuses.filter((s) => s.status === "missing");
  return (
    <section aria-labelledby="injection-heading" className="mb-6">
      <h2 id="injection-heading" className="mb-2 text-lg font-semibold">
        Injection pass rate
      </h2>
      {bad.length > 0 ? (
        <div className="mb-3">
          <Alert tone="danger" title="Prompt-injection protection regressed" role="alert" data-testid="injection-alert">
            {bad.map((s) => (
              <p key={s.version}>
                <strong>{s.version}</strong> passed {formatMetric("rate", s.rate!)} of the injection pages
                {s.status === "dropped" && s.previous !== null ? ` (its previous run passed ${formatMetric("rate", s.previous)})` : ""}.
                A hostile page changed what the model extracted. Do not ship this version until it is back at 100%.
              </p>
            ))}
          </Alert>
        </div>
      ) : null}
      {missing.length > 0 ? (
        <div className="mb-3">
          <Alert tone="warning" title="Injection pass rate is missing for some versions">
            <p>
              {missing.map((s) => s.version).join(", ")} {missing.length === 1 ? "has" : "have"} no injection result, so
              their protection is unknown, not assumed fine.
            </p>
          </Alert>
        </div>
      ) : null}
      <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {statuses.map((s) => {
          const st = STYLE[s.status];
          return (
            <li key={s.runId} className={cn("rounded-lg border p-4", st.box)} data-testid={`injection-${s.version}`} data-state={s.status}>
              <p className="text-sm font-medium">{s.version}</p>
              <p className={cn("text-3xl font-bold tabular-nums", st.text)}>{s.rate === null ? "Not measured" : formatMetric("rate", s.rate)}</p>
              <p className={cn("mt-1 flex items-center gap-1 text-sm font-semibold", st.text)}>
                <st.Icon aria-hidden="true" className="size-4 shrink-0" />
                {st.label}
              </p>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
