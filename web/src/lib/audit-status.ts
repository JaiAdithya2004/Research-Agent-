import type { AuditStatus } from "@/lib/audit-filters";

export type Tone = "info" | "success" | "warning" | "danger";
export type StatusIcon = "running" | "complete" | "capped" | "failed";

export interface StatusPresentation {
  label: string;
  /** Plain-language meaning, used as a tooltip/aria description. */
  meaning: string;
  tone: Tone;
  icon: StatusIcon;
}

/** Status is never conveyed by colour alone: every status has its own icon and its own word. */
export function presentStatus(status: AuditStatus): StatusPresentation {
  switch (status) {
    case "running":
      return { label: "Running", meaning: "The audit is still being worked on.", tone: "info", icon: "running" };
    case "complete":
      return { label: "Complete", meaning: "Every planned run finished.", tone: "success", icon: "complete" };
    case "capped":
      return {
        label: "Capped",
        meaning: "A limit stopped the audit early. The results are partial.",
        tone: "warning",
        icon: "capped",
      };
    case "failed":
      return { label: "Failed", meaning: "No run produced a usable answer.", tone: "danger", icon: "failed" };
  }
}

const CAP_LABELS: Record<string, string> = {
  cost_cap: "Cost cap",
  fetch_cap: "Page fetch cap",
  domain_cap: "Per-domain cap",
  max_steps: "Max steps",
};

const CAP_EXPLANATIONS: Record<string, string> = {
  cost_cap: "the spending limit for this audit was reached",
  fetch_cap: "the limit on pages fetched for this audit (30) was reached",
  domain_cap: "the limit on pages fetched from a single domain (5) was reached",
  max_steps: "the audit hit its maximum number of planned runs, so some questions were not asked",
};

/** Unknown codes fall back to the raw value. */
export function capLabel(cap: string): string {
  return CAP_LABELS[cap] ?? cap;
}

export function capExplanation(cap: string): string {
  return CAP_EXPLANATIONS[cap] ?? `the limit "${cap}" was reached`;
}
