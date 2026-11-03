import type { components } from "@/lib/api/schema.gen";
import type { Tone } from "@/lib/audit-status";
import { correctionLabel } from "@/lib/review-draft";

type Run = components["schemas"]["RunDetailOut"];

export type VerdictKind = "verified" | "flagged" | "reviewed" | "failed" | "pending" | "unchecked";
export type VerdictIcon = "check" | "flag" | "user-check" | "x" | "clock" | "minus";

export interface VerdictPresentation {
  kind: VerdictKind;
  label: string;
  detail: string | null;
  tone: Tone | "neutral";
  icon: VerdictIcon;
}

const REVIEWED_LABEL = {
  approve: "Approved by a reviewer",
  correct: "Corrected by a reviewer",
  reject: "Rejected by a reviewer",
} as const;

/** The verdict chip. A human review always wins over the machine verdict; failures are not verdicts. */
export function presentVerdict(run: Pick<Run, "status" | "error" | "verdict" | "review" | "brand_mentioned">): VerdictPresentation {
  if (run.status === "failed") {
    return { kind: "failed", label: "No usable answer", detail: run.error, tone: "danger", icon: "x" };
  }
  if (run.status === "pending") {
    return { kind: "pending", label: "Still running", detail: null, tone: "info", icon: "clock" };
  }
  if (run.review) {
    const corrected = correctionLabel(run.review.corrected_value);
    const parts = [corrected ? `Set to: ${corrected}.` : null, `Reason: ${run.review.reason}`, `By ${run.review.reviewer}.`];
    return {
      kind: "reviewed",
      label: REVIEWED_LABEL[run.review.action],
      detail: parts.filter(Boolean).join(" "),
      tone: "info",
      icon: "user-check",
    };
  }
  if (run.verdict?.status === "verified") {
    return { kind: "verified", label: "Verified", detail: run.verdict.reason, tone: "success", icon: "check" };
  }
  if (run.verdict?.status === "flagged") {
    return { kind: "flagged", label: "Flagged: needs review", detail: run.verdict.reason, tone: "warning", icon: "flag" };
  }
  return { kind: "unchecked", label: "Not checked", detail: "No verdict was recorded for this run.", tone: "neutral", icon: "minus" };
}
