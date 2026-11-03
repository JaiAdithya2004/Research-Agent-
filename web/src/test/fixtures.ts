import type { components } from "@/lib/api/schema.gen";

type Audit = components["schemas"]["AuditOut"];
type Summary = components["schemas"]["AuditSummary"];
type Run = components["schemas"]["RunSummaryOut"];
type Stability = components["schemas"]["QuestionStability"];

export function makeSummary(over: Partial<Summary> = {}): Summary {
  return {
    mention_rate: 0.5,
    stability: 0.8,
    flagged_count: 0,
    total_cost_usd: 0.1234,
    search_calls: 12,
    pages_fetched: 7,
    latency_p50_ms: 1200,
    latency_p90_ms: 3400,
    status: "complete",
    avg_position: 2.5,
    caps_hit: [],
    degraded: false,
    failed_runs: 0,
    mention_rate_by_mode: { grounded: 0.5 },
    mode_delta: null,
    question_stability: [],
    repeats_compared: 2,
    total_runs: 6,
    ...over,
  };
}

export function makeAudit(over: Partial<Audit> = {}, summary: Partial<Summary> | null = {}): Audit {
  const status = over.status ?? "complete";
  return {
    id: "aud-1",
    status,
    brand: "Acme",
    created_at: "2026-03-01T10:00:00Z",
    completed_at: status === "running" ? null : "2026-03-01T10:05:00Z",
    degraded: false,
    caps_hit: [],
    request: {
      brand: "Acme",
      aliases: [],
      category: "crm",
      competitors: [],
      questions: ["Q one?", "Q two?"],
      repeats: 3,
      search_mode: "grounded",
      research_depth: "cited_pages",
    },
    summary: summary === null ? null : makeSummary({ status, ...summary }),
    ...over,
  };
}

export function makeRun(over: Partial<Run> = {}): Run {
  return {
    id: "run-1",
    question_idx: 0,
    question: "Q one?",
    provider: "gemini",
    model: "gem-1",
    repeat_idx: 0,
    search_mode: "grounded",
    status: "ok",
    error: null,
    brand_mentioned: true,
    verdict_status: "verified",
    reviewed: false,
    degraded: false,
    latency_ms: 1000,
    cost_usd: 0.01,
    ...over,
  };
}

export function makeStability(over: Partial<Stability> = {}): Stability {
  return {
    question_idx: 0,
    provider: "gemini",
    model: "gem-1",
    search_mode: "grounded",
    outcomes: [true, true, false],
    agreement: 0.67,
    ...over,
  };
}
