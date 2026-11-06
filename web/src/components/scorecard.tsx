import { ArrowDown, ArrowRight, ArrowUp } from "lucide-react";
import type { Metric, ModeDeltaView } from "@/lib/scorecard";

export function Scorecard({ metrics }: { metrics: readonly Metric[] }) {
  return (
    <section aria-labelledby="scorecard-heading" className="mb-6">
      <h2 id="scorecard-heading" className="mb-3 text-lg font-semibold">
        Scorecard
      </h2>
      <dl className="grid grid-cols-2 gap-3 md:grid-cols-3">
        {metrics.map((m) => (
          <div key={m.id} className="rounded-lg border bg-card p-3" data-testid={`metric-${m.id}`}>
            <dt className="text-xs font-medium text-muted-foreground">{m.label}</dt>
            <dd className="mt-1 text-xl font-semibold tabular-nums">{m.value}</dd>
            {m.note ? <dd className="mt-1 text-xs text-muted-foreground">{m.note}</dd> : null}
          </div>
        ))}
      </dl>
    </section>
  );
}

const DIRECTION_ICON = { up: ArrowUp, down: ArrowDown, flat: ArrowRight } as const;
const DIRECTION_WORD = { up: "higher with search", down: "lower with search", flat: "no difference" } as const;

export function ModeDeltaCard({ view }: { view: ModeDeltaView }) {
  const Icon = DIRECTION_ICON[view.direction];
  return (
    <section aria-labelledby="delta-heading" className="mb-6 rounded-lg border bg-card p-4" data-testid="mode-delta">
      <h2 id="delta-heading" className="text-lg font-semibold">
        With search versus without
      </h2>
      <dl className="mt-3 grid grid-cols-3 gap-3">
        <div>
          <dt className="text-xs font-medium text-muted-foreground">With search</dt>
          <dd className="text-xl font-semibold tabular-nums">{view.grounded}</dd>
        </div>
        <div>
          <dt className="text-xs font-medium text-muted-foreground">Without search</dt>
          <dd className="text-xl font-semibold tabular-nums">{view.off}</dd>
        </div>
        <div>
          <dt className="text-xs font-medium text-muted-foreground">Difference</dt>
          <dd className="flex items-center gap-1 text-xl font-semibold tabular-nums">
            <Icon aria-hidden="true" className="size-5" />
            <span>{view.delta}</span>
            <span className="sr-only">({DIRECTION_WORD[view.direction]})</span>
          </dd>
        </div>
      </dl>
      <p className="mt-3 text-sm text-muted-foreground">{view.sentence}</p>
    </section>
  );
}
