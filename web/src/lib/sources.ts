import type { components } from "@/lib/api/schema.gen";
import { presentEvidence } from "@/lib/evidence";

type Source = components["schemas"]["SourceOut"];

/** The table shows this many rows until the user asks for all of them (the API does not paginate). */
export const SOURCE_ROW_CAP = 100;

export interface SourcesSummary {
  total: number;
  found: number;
  notFound: number;
  /** Pages we could not read. Never counted as "brand absent". */
  unreadable: number;
}

export function summarizeSources(sources: readonly Source[]): SourcesSummary {
  const s: SourcesSummary = { total: sources.length, found: 0, notFound: 0, unreadable: 0 };
  for (const src of sources) {
    const kind = presentEvidence(src).kind;
    if (kind === "found") s.found += 1;
    else if (kind === "not_found") s.notFound += 1;
    else s.unreadable += 1;
  }
  return s;
}

export function limitRows<T>(rows: readonly T[], showAll: boolean, cap = SOURCE_ROW_CAP): { shown: T[]; hidden: number } {
  if (showAll || rows.length <= cap) return { shown: [...rows], hidden: 0 };
  return { shown: rows.slice(0, cap), hidden: rows.length - cap };
}
