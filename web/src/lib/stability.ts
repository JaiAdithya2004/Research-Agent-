import type { components } from "@/lib/api/schema.gen";

type QuestionStability = components["schemas"]["QuestionStability"];
type Run = components["schemas"]["RunSummaryOut"];
export type RunSearchMode = QuestionStability["search_mode"];

/**
 * One dot per repeat. A failed repeat and a repeat that has not run are different marks from
 * "not mentioned": disagreement and gaps stay visible, they are never averaged away.
 */
export type DotKind = "mentioned" | "absent" | "failed" | "pending";

export interface Dot {
  kind: DotKind;
  /** 1-based, as a person counts. */
  repeat: number;
  runId: string | null;
  label: string;
}

export interface StabilityGroup {
  key: string;
  provider: string;
  model: string;
  searchMode: RunSearchMode;
  modeLabel: string;
  dots: Dot[];
  /** Share of usable repeats that agree with their majority; null with fewer than two usable repeats. */
  agreement: number | null;
  /** Some usable repeats mention the brand and some do not. */
  disagree: boolean;
  mentioned: number;
  usable: number;
}

export interface QuestionRow {
  index: number;
  question: string;
  groups: StabilityGroup[];
  disagree: boolean;
}

const LABELS: Record<DotKind, (n: number, running: boolean) => string> = {
  mentioned: (n) => `Repeat ${n}: brand mentioned`,
  absent: (n) => `Repeat ${n}: brand not mentioned`,
  failed: (n) => `Repeat ${n}: no usable answer, the run failed`,
  pending: (n, running) => `Repeat ${n}: ${running ? "not run yet" : "not run, the audit stopped before it"}`,
};

const MODE_LABELS: Record<RunSearchMode, string> = {
  grounded: "with search",
  tavily: "with Tavily search",
  off: "without search",
};
const MODE_ORDER: Record<RunSearchMode, number> = { grounded: 0, tavily: 1, off: 2 };

export function modeLabel(mode: RunSearchMode): string {
  return MODE_LABELS[mode];
}

export function buildQuestionRows(
  questions: readonly string[],
  stability: readonly QuestionStability[],
  runs: readonly Run[],
  opts: { repeats: number; running: boolean },
): QuestionRow[] {
  const runsByGroup = new Map<string, Run[]>();
  for (const r of runs) {
    const key = `${r.question_idx}|${r.provider}|${r.model}|${r.search_mode}`;
    runsByGroup.set(key, [...(runsByGroup.get(key) ?? []), r]);
  }
  for (const list of runsByGroup.values()) list.sort((a, b) => a.repeat_idx - b.repeat_idx);

  return questions.map((question, index) => {
    const groups = stability
      .filter((s) => s.question_idx === index)
      .sort((a, b) => (a.search_mode === b.search_mode ? a.model.localeCompare(b.model) : MODE_ORDER[a.search_mode] - MODE_ORDER[b.search_mode]))
      .map((s): StabilityGroup => {
        const key = `${s.question_idx}|${s.provider}|${s.model}|${s.search_mode}`;
        const groupRuns = runsByGroup.get(key) ?? [];
        const kinds: DotKind[] = s.outcomes.map((o) => (o === true ? "mentioned" : o === false ? "absent" : "failed"));
        while (kinds.length < opts.repeats) kinds.push("pending");
        const dots = kinds.map((kind, i): Dot => ({
          kind,
          repeat: i + 1,
          runId: kind === "pending" ? null : (groupRuns[i]?.id ?? null),
          label: LABELS[kind](i + 1, opts.running),
        }));
        const mentioned = s.outcomes.filter((o) => o === true).length;
        const absent = s.outcomes.filter((o) => o === false).length;
        return {
          key,
          provider: s.provider,
          model: s.model,
          searchMode: s.search_mode,
          modeLabel: modeLabel(s.search_mode),
          dots,
          agreement: s.agreement ?? null,
          disagree: mentioned > 0 && absent > 0,
          mentioned,
          usable: mentioned + absent,
        };
      });
    return { index, question, groups, disagree: groups.some((g) => g.disagree) };
  });
}

export function agreementText(group: Pick<StabilityGroup, "agreement" | "usable" | "mentioned">): string {
  if (group.agreement === null) return group.usable < 2 ? "needs 2 or more usable repeats to compare" : "agreement unknown";
  return `${Math.round(group.agreement * 100)}% agreement`;
}
