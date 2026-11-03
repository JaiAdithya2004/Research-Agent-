/**
 * Client-side cost estimate for the new-audit form. There is no pricing endpoint, so this is a
 * documented calculation (web/CONTRACT.md, "Cost estimate on the new-audit form"). It is an
 * estimate, never a quote: the server enforces the real cap.
 */
import type { ResearchDepth, SearchMode } from "@/lib/audit-request";

export const ASSUMPTIONS = {
  models: 2,
  queriesPerGroundedRun: 3,
  searchUsdPerQuery: 0.014,
  inputUsdPerMillion: 0.75,
  outputUsdPerMillion: 3.75,
  answerTokens: { input: 1500, output: 800 },
  extractionTokens: { input: 1200, output: 150 },
  pagesPerGroundedRun: 3,
  maxPageFetches: 30,
  defaultCapUsd: 0.75,
} as const;

export interface EstimateInput {
  /** Number of non-blank questions. */
  questions: number;
  repeats: number;
  searchMode: SearchMode;
  researchDepth: ResearchDepth;
}

export interface Estimate {
  runs: number;
  groundedRuns: number;
  modelCalls: { count: number; usd: number };
  extractionCalls: { count: number; usd: number };
  searchCalls: { count: number; usd: number };
  /** One classification call per fetched page: an upper bound, since a domain is classified once. */
  classificationCalls: { count: number; usd: number };
  /** Fetching is free but counts toward the 30-fetch cap. */
  pageFetches: { count: number; usd: 0 };
  totalUsd: number;
  capUsd: number;
  exceedsCap: boolean;
}

const A = ASSUMPTIONS;

function callUsd(tokens: { input: number; output: number }): number {
  return (tokens.input * A.inputUsdPerMillion + tokens.output * A.outputUsdPerMillion) / 1_000_000;
}

/** Remove floating point noise (and make the cap comparison exact to a millionth of a dollar). */
const money = (n: number): number => Math.round(n * 1e6) / 1e6;

export function estimateAudit(input: EstimateInput): Estimate {
  const questions = Math.max(0, Math.floor(input.questions));
  const repeats = Math.max(0, Math.floor(input.repeats));
  const perMode = questions * repeats * A.models;

  const modes = input.searchMode === "both" ? 2 : 1;
  const runs = perMode * modes;
  const groundedRuns = input.searchMode === "off" ? 0 : perMode;

  const searchCount = groundedRuns * A.queriesPerGroundedRun;
  const fetchCount =
    input.researchDepth === "cited_pages" ? Math.min(A.maxPageFetches, A.pagesPerGroundedRun * groundedRuns) : 0;

  const modelCalls = { count: runs, usd: money(runs * callUsd(A.answerTokens)) };
  const extractionCalls = { count: runs, usd: money(runs * callUsd(A.extractionTokens)) };
  const searchCalls = { count: searchCount, usd: money(searchCount * A.searchUsdPerQuery) };
  const classificationCalls = { count: fetchCount, usd: money(fetchCount * callUsd(A.extractionTokens)) };

  const totalUsd = money(modelCalls.usd + extractionCalls.usd + searchCalls.usd + classificationCalls.usd);
  return {
    runs,
    groundedRuns,
    modelCalls,
    extractionCalls,
    searchCalls,
    classificationCalls,
    pageFetches: { count: fetchCount, usd: 0 },
    totalUsd,
    capUsd: A.defaultCapUsd,
    exceedsCap: totalUsd > A.defaultCapUsd,
  };
}
