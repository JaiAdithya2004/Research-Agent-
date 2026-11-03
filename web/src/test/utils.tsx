import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { ReviewTray } from "@/components/review-tray";
import { ReviewOutboxProvider } from "@/hooks/use-review-outbox";

export function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  return { client, ...render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>) };
}

export function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });
}

export function auditItem(over: Record<string, unknown> = {}) {
  return {
    id: "a1",
    brand: "Acme",
    status: "complete",
    created_at: "2026-03-01T10:00:00Z",
    completed_at: "2026-03-01T10:05:00Z",
    search_mode: "grounded",
    mention_rate: 0.5,
    flagged_count: 0,
    total_cost_usd: 0.123,
    degraded: false,
    total_runs: 6,
    failed_runs: 0,
    caps_hit: [],
    ...over,
  };
}

/** Like renderWithClient, plus the review outbox and its tray (undo toast), as in the real app. */
export function renderWithApp(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <ReviewOutboxProvider>
          {ui}
          <ReviewTray />
        </ReviewOutboxProvider>
      </QueryClientProvider>,
    ),
  };
}

export function runDetail(over: Record<string, unknown> = {}) {
  return {
    id: "run-1",
    audit_id: "aud-1",
    question: "Best CRM for startups?",
    provider: "gemini",
    model: "gem-1",
    repeat_idx: 0,
    search_mode: "grounded",
    status: "ok",
    error: null,
    raw_answer: "Top picks: Acme CRM and Rival. See https://a.com.",
    mention: {
      brand_mentioned: true,
      position: 1,
      quote: "Acme CRM",
      competitors_seen: ["Rival"],
      citations: [],
      sentiment: "pos",
    },
    verdict: { status: "verified", reason: "Quote found in the answer." },
    findings: [],
    highlights: [{ start: 11, end: 19 }],
    citations: [],
    review: null,
    brand_mentioned: true,
    degraded: false,
    latency_ms: 1200,
    cost_usd: 0.01,
    ...over,
  };
}
