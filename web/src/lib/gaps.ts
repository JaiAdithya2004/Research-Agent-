import type { components } from "@/lib/api/schema.gen";

type Entry = components["schemas"]["GapEntryOut"];

/** The gap report shows this many domains until the user asks for the rest. */
export const GAP_PREVIEW = 10;

/** Most frequently cited first; ties by domain so the order is stable. The API already ranks, this makes it a guarantee. */
export function rankGaps(entries: readonly Entry[]): Entry[] {
  return [...entries].sort((a, b) => b.frequency - a.frequency || a.domain.localeCompare(b.domain));
}

export function visibleGaps(ranked: readonly Entry[], showAll: boolean): { shown: Entry[]; hidden: number } {
  if (showAll || ranked.length <= GAP_PREVIEW) return { shown: [...ranked], hidden: 0 };
  return { shown: ranked.slice(0, GAP_PREVIEW), hidden: ranked.length - GAP_PREVIEW };
}
