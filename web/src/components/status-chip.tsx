import { AlertTriangle, CheckCircle2, Gauge, Loader2, XCircle } from "lucide-react";
import { capLabel, presentStatus, type StatusIcon, type Tone } from "@/lib/audit-status";
import type { AuditStatus } from "@/lib/audit-filters";
import { cn } from "@/lib/utils";

const TONE: Record<Tone, string> = {
  info: "border-info/50 bg-info-bg text-info",
  success: "border-success/50 bg-success-bg text-success",
  warning: "border-warning/60 bg-warning-bg text-warning",
  danger: "border-destructive/50 bg-destructive-bg text-destructive",
};

const ICON: Record<StatusIcon, typeof Loader2> = {
  running: Loader2,
  complete: CheckCircle2,
  capped: Gauge,
  failed: XCircle,
};

/** Status is shown with an icon AND a word; degraded and capped are surfaced as extra chips. */
export function StatusChip({ status, className }: { status: AuditStatus; className?: string }) {
  const p = presentStatus(status);
  const Icon = ICON[p.icon];
  return (
    <span
      title={p.meaning}
      className={cn(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-semibold",
        TONE[p.tone],
        className,
      )}
    >
      <Icon aria-hidden="true" className={cn("size-3.5", p.icon === "running" && "motion-safe:animate-spin")} />
      {p.label}
    </span>
  );
}

export function DegradedChip() {
  return (
    <span
      title="Search was unavailable for some runs, so they fell back to answering without search."
      className="inline-flex items-center gap-1 whitespace-nowrap rounded-full border border-warning/60 bg-warning-bg px-2 py-0.5 text-xs font-semibold text-warning"
    >
      <AlertTriangle aria-hidden="true" className="size-3.5" />
      Degraded
    </span>
  );
}

export function CapsChips({ caps }: { caps: readonly string[] }) {
  return (
    <>
      {caps.map((cap) => (
        <span
          key={cap}
          className="inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium text-muted-foreground"
        >
          <Gauge aria-hidden="true" className="size-3.5" />
          {capLabel(cap)} hit
        </span>
      ))}
    </>
  );
}
