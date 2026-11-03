import { Loader2, RefreshCw, Radio, CheckCircle2 } from "lucide-react";
import type { ConnectionState } from "@/lib/audit-events";
import type { ProgressView } from "@/lib/audit-progress";

const CONNECTION: Record<ConnectionState, { label: string; Icon: typeof Radio; description: string }> = {
  connecting: { label: "Connecting", Icon: Loader2, description: "Opening the live connection." },
  live: { label: "Live", Icon: Radio, description: "Results update as they arrive." },
  reconnecting: { label: "Reconnecting", Icon: RefreshCw, description: "The live connection dropped. Trying again, nothing is lost." },
  finished: { label: "Finished", Icon: CheckCircle2, description: "The audit is over." },
};

export function ConnectionBadge({ state }: { state: ConnectionState }) {
  const { label, Icon, description } = CONNECTION[state];
  return (
    <span
      title={description}
      className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-semibold"
      data-testid="connection-state"
    >
      <Icon aria-hidden="true" className={state === "connecting" || state === "reconnecting" ? "size-3.5 motion-safe:animate-spin" : "size-3.5"} />
      <span className="sr-only">Connection: </span>
      {label}
    </span>
  );
}

export function ProgressPanel({ view, announcement }: { view: ProgressView; announcement: string }) {
  const known = view.fraction !== null;
  return (
    <section aria-labelledby="progress-heading" className="mb-6 rounded-lg border bg-card p-4">
      <h2 id="progress-heading" className="text-lg font-semibold">
        Progress
      </h2>
      <p className="mt-1 text-sm" data-testid="progress-text">
        {view.text}
        {view.failed > 0 ? `, ${view.failed} failed` : ""}
      </p>
      <div
        role="progressbar"
        aria-label="Audit progress"
        aria-valuemin={0}
        aria-valuemax={known ? (view.planned ?? undefined) : undefined}
        aria-valuenow={known ? view.done : undefined}
        aria-valuetext={view.text}
        className="mt-2 h-2.5 w-full overflow-hidden rounded-full bg-muted"
      >
        <div
          className={known ? "h-full bg-primary transition-[width]" : "h-full w-1/3 bg-primary motion-safe:animate-pulse"}
          style={known ? { width: `${Math.round((view.fraction ?? 0) * 100)}%` } : undefined}
        />
      </div>
      {/* Polite and quantised: it changes about ten times per audit, not once per event. */}
      <p role="status" aria-live="polite" className="sr-only" data-testid="progress-announcement">
        {announcement}
      </p>
    </section>
  );
}
