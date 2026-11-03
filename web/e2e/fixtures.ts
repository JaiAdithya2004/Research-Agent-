/**
 * Fixtures for the fake API, typed against the generated client types (src/lib/api/schema.gen.ts),
 * so a fixture that drifts from openapi.json fails `npm run typecheck` instead of passing silently.
 */
import type { components } from "../src/lib/api/schema.gen";

type S = components["schemas"];

export type AuditOut = S["AuditOut"];
export type AuditListItem = S["AuditListItem"];
export type RunSummary = S["RunSummaryOut"];
export type RunDetail = S["RunDetailOut"];
export type QueueItem = S["QueueItem"];
export type SourceOut = S["SourceOut"];
export type SourceDetail = S["SourceDetailOut"];
export type GapReport = S["GapReportOut"];
export type EvalRun = S["EvalRunOut"];

export interface StreamScript {
  events: { type: string; data: Record<string, unknown> }[];
  finalAudit?: AuditOut;
  finalRuns?: RunSummary[];
}

export interface FakeState {
  audits: AuditOut[];
  auditList: AuditListItem[];
  runs: Record<string, RunSummary[]>;
  runDetails: Record<string, RunDetail>;
  queue: QueueItem[];
  sources: Record<string, SourceOut[]>;
  sourceDetails: Record<string, Record<string, SourceDetail>>;
  gaps: Record<string, GapReport>;
  evals: EvalRun[];
  streams: Record<string, StreamScript>;
  createResponse: AuditOut | null;
}

export const emptyState = (): FakeState => ({
  audits: [],
  auditList: [],
  runs: {},
  runDetails: {},
  queue: [],
  sources: {},
  sourceDetails: {},
  gaps: {},
  evals: [],
  streams: {},
  createResponse: null,
});

const hoursAgo = (h: number) => new Date(Date.now() - h * 3600_000).toISOString();

// ---------------------------------------------------------------- audits

export const QUESTIONS = ["What is the best CRM for startups?", "Which CRM has the best email tools?"];
export const REPEATS = 3;
export const PLANNED = QUESTIONS.length * REPEATS;

const request = (): S["AuditRequest"] => ({
  brand: "Acme",
  aliases: [],
  category: "crm",
  competitors: ["Globex"],
  questions: QUESTIONS,
  repeats: REPEATS,
  search_mode: "grounded",
  research_depth: "cited_pages",
});

const summary = (over: Partial<S["AuditSummary"]> = {}): S["AuditSummary"] => ({
  mention_rate: 0.5,
  stability: 0.8,
  flagged_count: 1,
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
  question_stability: QUESTIONS.map((_, i) => ({
    question_idx: i,
    provider: "gemini",
    model: "gem-1",
    search_mode: "grounded" as const,
    outcomes: [true, true, false],
    agreement: 0.67,
  })),
  repeats_compared: 3,
  total_runs: PLANNED,
  ...over,
});

export function audit(id: string, status: AuditOut["status"], over: Partial<AuditOut> = {}): AuditOut {
  return {
    id,
    status,
    brand: "Acme",
    created_at: hoursAgo(1),
    completed_at: status === "running" ? null : hoursAgo(0.9),
    degraded: false,
    caps_hit: [],
    request: request(),
    summary: status === "running" ? null : summary({ status }),
    ...over,
  };
}

export function listItem(a: AuditOut): AuditListItem {
  return {
    id: a.id,
    brand: a.brand,
    status: a.status,
    created_at: a.created_at,
    completed_at: a.completed_at,
    degraded: a.degraded,
    caps_hit: a.caps_hit,
    failed_runs: 0,
    flagged_count: a.summary?.flagged_count ?? 0,
    mention_rate: a.summary?.mention_rate ?? null,
    search_mode: "grounded",
    total_cost_usd: a.summary?.total_cost_usd ?? 0,
    total_runs: a.summary?.total_runs ?? 0,
  };
}

export function runRows(): RunSummary[] {
  return QUESTIONS.flatMap((question, q) =>
    Array.from({ length: REPEATS }, (_, r): RunSummary => ({
      id: `run-${q}-${r}`,
      question_idx: q,
      question,
      provider: "gemini",
      model: "gem-1",
      repeat_idx: r,
      search_mode: "grounded",
      status: "ok",
      error: null,
      brand_mentioned: r < 2,
      verdict_status: "verified",
      reviewed: false,
      degraded: false,
      latency_ms: 1000,
      cost_usd: 0.01,
    })),
  );
}

const step = (q: number, r: number) => ({
  question_idx: q,
  provider: "gemini",
  model: "gem-1",
  repeat_idx: r,
  search_mode: "grounded",
});

/** audit_started, then run_started + run_verified for every run, then audit_completed. */
export function completionScript(finalAudit: AuditOut, finalRuns: RunSummary[]): StreamScript {
  const events: StreamScript["events"] = [{ type: "audit_started", data: { planned_steps: PLANNED } }];
  for (let q = 0; q < QUESTIONS.length; q++) {
    for (let r = 0; r < REPEATS; r++) {
      events.push({ type: "run_started", data: { step: step(q, r) } });
      events.push({ type: "run_verified", data: { step: step(q, r) } });
    }
  }
  events.push({ type: "audit_completed", data: {} });
  return { events, finalAudit, finalRuns };
}

/** Number of events in a completionScript: 1 + 2 * runs + 1. */
export const EVENT_COUNT = 2 + 2 * PLANNED;

// ---------------------------------------------------------------- run detail, queue

export const ANSWER = "For startups, Acme is a strong CRM choice because of its pipeline view. Globex is another option.";
export const QUOTE = "Acme is a strong CRM choice";

export function runDetail(id: string, auditId: string, over: Partial<RunDetail> = {}): RunDetail {
  const start = ANSWER.indexOf(QUOTE);
  return {
    id,
    audit_id: auditId,
    question: QUESTIONS[0]!,
    provider: "gemini",
    model: "gem-1",
    repeat_idx: 0,
    search_mode: "grounded",
    status: "ok",
    error: null,
    raw_answer: ANSWER,
    mention: { brand_mentioned: true, position: 1, quote: QUOTE, competitors_seen: ["Globex"], citations: [], sentiment: "pos" },
    verdict: { status: "flagged", reason: "The quote could not be matched exactly in the answer." },
    findings: [{ code: "quote_fuzzy", reason: "Matched only after ignoring punctuation.", status: "flagged" }],
    highlights: [{ start, end: start + QUOTE.length }],
    citations: [
      {
        url: "https://acme.example/pricing",
        domain: "acme.example",
        title: "Acme pricing",
        source: "grounding_metadata",
        unverified: false,
        evidence: { brand_on_page: true, domain: "acme.example", fetch_reason: "ok", fetch_status: "ok", source_id: "src-found", source_type: "own_site" },
      },
      {
        url: "https://reviews.example/crm",
        domain: "reviews.example",
        title: null,
        source: "grounding_metadata",
        unverified: false,
        evidence: { brand_on_page: null, domain: "reviews.example", fetch_reason: "http_not_found", fetch_status: "unreachable", source_id: "src-gone", source_type: "review" },
      },
      { url: "https://blog.example/post", domain: "blog.example", title: null, source: "answer_text", unverified: true, evidence: null },
    ],
    review: null,
    brand_mentioned: true,
    degraded: false,
    latency_ms: 1100,
    cost_usd: 0.012,
    ...over,
  };
}

export function queueItem(runId: string, auditId: string, question: string): QueueItem {
  const start = ANSWER.indexOf(QUOTE);
  return {
    run_id: runId,
    audit_id: auditId,
    brand: "Acme",
    question,
    provider: "gemini",
    model: "gem-1",
    search_mode: "grounded",
    repeat_idx: 0,
    verdict_reason: "The quote could not be matched exactly in the answer.",
    quote: QUOTE,
    brand_mentioned: true,
    position: 1,
    raw_answer: ANSWER,
    highlights: [{ start, end: start + QUOTE.length }],
    created_at: hoursAgo(0.5),
  };
}

// ---------------------------------------------------------------- sources

export const SNAPSHOT_TEXT =
  "Acme pricing. Plans start at 12 dollars per seat. Acme is the CRM that startups pick for pipeline tracking. Contact sales for volume discounts.";
export const SNAPSHOT_QUOTE = "Acme is the CRM that startups pick for pipeline tracking";

function sourceBase(id: string, over: Partial<SourceOut>): SourceOut {
  return {
    id,
    url: `https://${id}.example/page`,
    final_url: null,
    domain: `${id}.example`,
    fetch_status: "ok",
    fetch_reason: "ok",
    brand_on_page: true,
    quote: null,
    source_type: "other",
    classification: "other",
    snapshot_id: `snap-${id}`,
    snapshot_fetched_at: hoursAgo(3),
    ...over,
  };
}

export const FOUND_SOURCE = sourceBase("src-found", {
  url: "https://acme.example/pricing",
  domain: "acme.example",
  quote: SNAPSHOT_QUOTE,
  source_type: "own_site",
});
export const NOT_FOUND_SOURCE = sourceBase("src-absent", { brand_on_page: false, source_type: "news", url: "https://news.example/a", domain: "news.example" });
export const UNREADABLE_SOURCE = sourceBase("src-gone", {
  url: "https://reviews.example/crm",
  domain: "reviews.example",
  fetch_status: "unreachable",
  fetch_reason: "http_not_found",
  brand_on_page: null,
  source_type: "review",
  snapshot_id: null,
  snapshot_fetched_at: null,
});

export function sourceDetail(s: SourceOut, over: Partial<SourceDetail> = {}): SourceDetail {
  return { ...s, snapshot_text: s.snapshot_id ? SNAPSHOT_TEXT : null, content_hash: s.snapshot_id ? "abcdef1234567890" : null, ...over };
}

export function gapReport(auditId: string): GapReport {
  return {
    audit_id: auditId,
    entries: [
      { domain: "g2.example", frequency: 4, competitors: ["Globex"], source_ids: ["src-absent"] },
      { domain: "capterra.example", frequency: 2, competitors: ["Globex", "Initech"], source_ids: [] },
    ],
  };
}

// ---------------------------------------------------------------- evals

const stats = (mean: number): S["MetricStats"] => ({ mean, stdev: 0.01, min: mean - 0.01, max: mean + 0.01, values: [mean - 0.01, mean, mean + 0.01] });

export function evalRun(id: string, version: string, startedAt: string, accuracy: number): EvalRun {
  const metrics: Record<string, S["MetricStats"]> = {
    extraction_accuracy: stats(accuracy),
    false_mention_rate: stats(0.05),
    flag_rate: stats(0.2),
    source_check_accuracy: stats(0.9),
    injection_pass_rate: stats(1),
    total_cost_usd: stats(1),
    latency_p50_ms: stats(1000),
    latency_p90_ms: stats(2000),
  };
  return {
    id,
    prompt_version: version,
    classifier_prompt_version: "v1",
    repeats: 3,
    extractor_model: "gem-1",
    started_at: startedAt,
    counts: { answers: 18, pages: 12, injection: 8 },
    metrics,
    per_repeat: [],
    notes: [],
  };
}

// ---------------------------------------------------------------- the whole-app scenario used by the page checks

export const IDS = {
  running: "aud-run",
  complete: "aud-done",
  created: "aud-new",
  reviewRun: "run-flag-1",
  reviewRun2: "run-flag-2",
  emptyAudit: "aud-empty",
  capped: "aud-capped",
  failed: "aud-failed",
};

export function appState(): FakeState {
  const running = audit(IDS.running, "running");
  const complete = audit(IDS.complete, "complete");
  const emptyAudit = audit(IDS.emptyAudit, "complete");
  const capped = audit(IDS.capped, "capped", { degraded: true, caps_hit: ["cost_cap", "fetch_cap"], summary: summary({ status: "capped", degraded: true, caps_hit: ["cost_cap", "fetch_cap"], failed_runs: 2 }) });
  const failed = audit(IDS.failed, "failed", { summary: null });
  const rows = runRows();
  return {
    ...emptyState(),
    audits: [running, complete, emptyAudit, capped, failed],
    auditList: [listItem(running), listItem(complete), listItem(capped), listItem(failed)],
    runs: { [IDS.running]: [], [IDS.complete]: rows, [IDS.emptyAudit]: [], [IDS.capped]: rows.map((r, i) => (i < 2 ? { ...r, status: "failed" as const, error: "timeout", brand_mentioned: null, verdict_status: null } : r)), [IDS.failed]: [] },
    runDetails: {
      "run-0-0": runDetail("run-0-0", IDS.complete, { verdict: { status: "verified", reason: "Quote found in the answer." }, findings: [] }),
      [IDS.reviewRun]: runDetail(IDS.reviewRun, IDS.complete),
      [IDS.reviewRun2]: runDetail(IDS.reviewRun2, IDS.complete, { question: QUESTIONS[1]! }),
    },
    queue: [queueItem(IDS.reviewRun, IDS.complete, QUESTIONS[0]!), queueItem(IDS.reviewRun2, IDS.complete, QUESTIONS[1]!)],
    sources: { [IDS.complete]: [FOUND_SOURCE, NOT_FOUND_SOURCE, UNREADABLE_SOURCE], [IDS.emptyAudit]: [] },
    sourceDetails: {
      [IDS.complete]: {
        [FOUND_SOURCE.id]: sourceDetail(FOUND_SOURCE),
        [NOT_FOUND_SOURCE.id]: sourceDetail(NOT_FOUND_SOURCE, { snapshot_text: "A news page that never mentions the product." }),
        [UNREADABLE_SOURCE.id]: sourceDetail(UNREADABLE_SOURCE),
      },
    },
    gaps: { [IDS.complete]: gapReport(IDS.complete), [IDS.emptyAudit]: { audit_id: IDS.emptyAudit, entries: [] } },
    evals: [evalRun("ev-1", "v1", "2026-03-01T10:00:00Z", 0.8), evalRun("ev-2", "v2", "2026-03-02T10:00:00Z", 0.9)],
    streams: { [IDS.running]: completionScript(audit(IDS.running, "complete"), rows) },
    createResponse: audit(IDS.created, "running", { brand: "Initech" }),
  };
}
