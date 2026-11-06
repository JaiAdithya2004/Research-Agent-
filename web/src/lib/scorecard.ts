import type { components } from "@/lib/api/schema.gen";
import { formatRate, formatSignedPoints } from "@/lib/format";

type Audit = components["schemas"]["AuditOut"];
type Summary = components["schemas"]["AuditSummary"];

/** True when at least one run produced a usable answer. Without one, a rate is "unknown", not 0%. */
export function hasUsableRuns(summary: Summary | null | undefined): summary is Summary {
  return !!summary && (summary.total_runs ?? 0) - (summary.failed_runs ?? 0) > 0;
}

export interface Metric {
  id: string;
  label: string;
  value: string;
  note?: string;
}

export function scorecardMetrics(audit: Audit): Metric[] {
  const s = audit.summary;
  const usable = hasUsableRuns(s);
  const searched = audit.request.search_mode !== "off";
  const dash = "—";
  return [
    {
      id: "mention-rate",
      label: searched ? "Mention rate (with search)" : "Mention rate (no search)",
      value: usable ? formatRate(s.mention_rate) : dash,
      note: usable ? "Confirmed mentions over usable runs" : "No usable answers yet",
    },
    {
      id: "avg-position",
      label: "Average position",
      value: usable && s.avg_position != null ? s.avg_position.toFixed(1) : dash,
      note: usable && s.avg_position != null ? "Where the brand first appears; 1 is first" : "No position recorded",
    },
    {
      id: "flagged",
      label: "Needs your review",
      value: s ? String(s.flagged_count) : dash,
      note: "Waiting for review, not yet counted",
    },
  ];
}

/** One plain sentence for the audit report, or null when no answer was usable. */
export function reportHeadline(audit: Audit): string | null {
  const s = audit.summary;
  if (!hasUsableRuns(s)) return null;
  const position = s.avg_position != null ? `, first appearing at position ${s.avg_position.toFixed(1)} on average` : "";
  return `${audit.brand} was mentioned in ${formatRate(s.mention_rate)} of the AI answers${position}.`;
}

export interface ModeDeltaView {
  grounded: string;
  off: string;
  delta: string;
  direction: "up" | "down" | "flat";
  sentence: string;
}

/** Only for audits that ran in "both" mode (mode_delta is null otherwise). */
export function modeDeltaView(summary: Summary | null | undefined): ModeDeltaView | null {
  if (!summary || summary.mode_delta == null) return null;
  const grounded = summary.mention_rate_by_mode?.grounded;
  const off = summary.mention_rate_by_mode?.off;
  const delta = summary.mode_delta;
  const direction = Math.round(delta * 100) === 0 ? "flat" : delta > 0 ? "up" : "down";
  const g = grounded === undefined ? "unknown" : formatRate(grounded);
  const o = off === undefined ? "unknown" : formatRate(off);
  const sentence =
    direction === "flat"
      ? `The brand appeared about as often with search (${g}) as without (${o}).`
      : direction === "up"
        ? `The brand appeared more often with search (${g}) than without (${o}).`
        : `The brand appeared less often with search (${g}) than without (${o}).`;
  return { grounded: g, off: o, delta: formatSignedPoints(delta), direction, sentence };
}
