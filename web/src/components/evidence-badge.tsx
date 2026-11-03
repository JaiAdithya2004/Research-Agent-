import { Ban, CheckCircle2, Minus, SearchX } from "lucide-react";
import { FETCH_STATUS_LABEL, presentEvidence, type EvidenceIcon, type FetchStatus } from "@/lib/evidence";
import type { components } from "@/lib/api/schema.gen";
import { cn } from "@/lib/utils";

type Evidence = components["schemas"]["EvidenceBrief"] | components["schemas"]["SourceOut"];

const ICON: Record<EvidenceIcon, typeof Ban> = { check: CheckCircle2, "search-x": SearchX, ban: Ban, minus: Minus };

const TONE = {
  info: "border-info/50 bg-info-bg text-info",
  success: "border-success/50 bg-success-bg text-success",
  warning: "border-warning/60 bg-warning-bg text-warning",
  danger: "border-destructive/50 bg-destructive-bg text-destructive",
  neutral: "border-border bg-muted text-muted-foreground",
} as const;

/**
 * What we know about the brand on a cited page. The three real outcomes look different on purpose:
 * found (check, solid green), brand not on a page we read (magnifier, solid blue), and a page we
 * could not read (prohibition sign, DASHED amber, "Could not read" wording). Never colour alone.
 */
export function EvidenceBadge({ evidence }: { evidence: Evidence | null }) {
  const p = presentEvidence(evidence);
  const Icon = ICON[p.icon];
  return (
    <span
      title={p.meaning}
      data-evidence-kind={p.kind}
      className={cn(
        "inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-semibold",
        p.kind === "unreadable" ? "border-dashed border-2" : p.kind === "not_researched" ? "border-dotted" : "",
        TONE[p.tone],
      )}
    >
      <Icon aria-hidden="true" className="size-3.5 shrink-0" />
      {p.label}
    </span>
  );
}

const STATUS_TONE: Record<FetchStatus, keyof typeof TONE> = {
  ok: "success",
  unreachable: "warning",
  blocked: "warning",
  paywalled: "warning",
  too_large: "warning",
  error: "warning",
};

/** The fetch status as its own chip, next to (not instead of) the brand result. */
export function FetchStatusChip({ status }: { status: FetchStatus | null }) {
  if (status === null) {
    return (
      <span className={cn("inline-flex items-center rounded-md border border-dotted px-2 py-0.5 text-xs font-medium", TONE.neutral)}>
        Not fetched
      </span>
    );
  }
  return (
    <span className={cn("inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium", TONE[STATUS_TONE[status]])}>
      {FETCH_STATUS_LABEL[status]}
    </span>
  );
}
