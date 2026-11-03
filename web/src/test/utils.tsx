import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";

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
