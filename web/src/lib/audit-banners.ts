import type { components } from "@/lib/api/schema.gen";
import { capExplanation, capLabel, type Tone } from "@/lib/audit-status";
import { pluralize } from "@/lib/format";

type Audit = components["schemas"]["AuditOut"];
type Run = components["schemas"]["RunSummaryOut"];

export type BannerId = "failed" | "capped" | "degraded" | "partial" | "running" | "flagged";

export interface Banner {
  id: BannerId;
  tone: Tone;
  title: string;
  /** What happened, in plain language. */
  body: string;
  /** What it means for the numbers on this page. */
  meaning: string;
  action?: { href: string; label: string };
}

function joinList(items: string[]): string {
  if (items.length <= 1) return items[0] ?? "";
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

/** Each condition gets its own banner so that none is hidden behind another. */
export function buildBanners(audit: Audit, runs: readonly Run[] | undefined): Banner[] {
  const out: Banner[] = [];
  const summary = audit.summary;
  const caps = summary?.caps_hit?.length ? summary.caps_hit : (audit.caps_hit ?? []);
  const total = summary?.total_runs ?? 0;
  const failedRuns = summary?.failed_runs ?? 0;

  if (audit.status === "failed") {
    out.push({
      id: "failed",
      tone: "danger",
      title: "No usable answers",
      body: "None of the runs produced a usable answer, so there is nothing to score.",
      meaning: "Rates and positions are not available. They are shown as a dash, not as zero.",
    });
  }

  if (audit.status === "capped" || caps.length > 0) {
    const reasons = caps.map(capExplanation);
    out.push({
      id: "capped",
      tone: "warning",
      title: "Stopped at a limit: the results are partial",
      body:
        caps.length > 0
          ? `The audit stopped early because ${joinList(reasons)}. Limits hit: ${caps.map(capLabel).join(", ")}.`
          : "The audit stopped early because a limit was reached.",
      meaning:
        "Only the runs that finished are counted. Repeats that did not run are marked as not run in the grid, so a rate here may differ from a full audit.",
    });
  }

  if (audit.degraded || summary?.degraded) {
    const n = runs?.filter((r) => r.degraded).length ?? 0;
    out.push({
      id: "degraded",
      tone: "warning",
      title: "Search was unavailable for some runs",
      body: `${n > 0 ? `${pluralize(n, "run")} ` : "Some runs "}fell back to answering without search because the search tool was unavailable.`,
      meaning:
        "Those runs show what the model knows by itself, not what it says with web search, so the searched numbers rest on fewer searched runs.",
    });
  }

  if (failedRuns > 0 && audit.status !== "failed") {
    out.push({
      id: "partial",
      tone: "warning",
      title: `${pluralize(failedRuns, "run")} failed`,
      body: `${failedRuns} of ${total} runs did not produce a usable answer (a model call or the extraction failed).`,
      meaning: "Failed runs are left out of the rates and shown with a cross in the grid. The rates use only the usable runs.",
    });
  }

  if (audit.status === "running") {
    out.push({
      id: "running",
      tone: "info",
      title: "Still running",
      body: "Answers are still coming in.",
      meaning: "The numbers on this page are provisional and change as runs finish.",
    });
  }

  const flagged = summary?.flagged_count ?? 0;
  if (flagged > 0) {
    out.push({
      id: "flagged",
      tone: "info",
      title: `${pluralize(flagged, "flagged mention")} waiting for review`,
      body: "The verifier could not confirm these claims against the answer text, so a person needs to decide.",
      meaning: "Flagged mentions are not counted in the mention rate until someone approves them.",
      action: { href: `/review?audit_id=${encodeURIComponent(audit.id)}`, label: "Open the review queue for this audit" },
    });
  }
  return out;
}
