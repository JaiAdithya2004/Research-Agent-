import type { components } from "@/lib/api/schema.gen";
import { citationOrigin, presentEvidence } from "@/lib/evidence";

type Citation = components["schemas"]["CitationOut"];

export interface CitationSummary {
  total: number;
  grounded: number;
  proseOnly: number;
  found: number;
  notFound: number;
  unreadable: number;
  notResearched: number;
  /** Some citations could not be checked: the view is partial. */
  partial: boolean;
}

export function summarizeCitations(citations: readonly Citation[]): CitationSummary {
  const s: CitationSummary = { total: citations.length, grounded: 0, proseOnly: 0, found: 0, notFound: 0, unreadable: 0, notResearched: 0, partial: false };
  for (const c of citations) {
    if (citationOrigin(c).kind === "grounding") s.grounded += 1;
    else s.proseOnly += 1;
    const kind = presentEvidence(c.evidence).kind;
    if (kind === "found") s.found += 1;
    else if (kind === "not_found") s.notFound += 1;
    else if (kind === "unreadable") s.unreadable += 1;
    else s.notResearched += 1;
  }
  s.partial = s.unreadable + s.notResearched > 0;
  return s;
}
