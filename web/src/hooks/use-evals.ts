"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getApi } from "@/lib/api/client";
import { unwrap } from "@/lib/api/errors";
import { METRICS, buildComparison, comparabilityWarnings, injectionStatuses, latestPerVersion, trendSeries } from "@/lib/evals";

/** How many stored runs we ask for. Getting exactly this many back means older ones were cut off. */
export const EVAL_RUN_LIMIT = 100;

export function useEvalRuns() {
  return useQuery({
    queryKey: ["evals", "runs", EVAL_RUN_LIMIT],
    queryFn: async ({ signal }) =>
      unwrap(await getApi().GET("/evals/runs", { params: { query: { limit: EVAL_RUN_LIMIT } }, signal }), "The eval runs"),
  });
}

/** Everything the evals page shows, derived in one place so the components only render. */
export function useEvalsPage() {
  const query = useEvalRuns();
  const [baselineChoice, setBaselineChoice] = useState<string | null>(null);
  const runs = query.data;

  const view = useMemo(() => {
    if (!runs || runs.length === 0) return null;
    const latest = latestPerVersion(runs);
    const comparison = buildComparison(latest, baselineChoice);
    return {
      latest,
      comparison,
      injection: injectionStatuses(latest, runs),
      trends: METRICS.map((metric) => ({ metric, series: trendSeries(runs, metric.key) })),
      warnings: comparabilityWarnings(latest),
      notes: latest.filter((r) => (r.notes ?? []).length > 0).map((r) => ({ version: r.prompt_version, notes: r.notes ?? [] })),
      /** A version that lacks some metric: its column has gaps. */
      incomplete: latest.filter((r) => METRICS.some((m) => !r.metrics[m.key])).map((r) => r.prompt_version),
      capped: runs.length >= EVAL_RUN_LIMIT,
      totalRuns: runs.length,
    };
  }, [runs, baselineChoice]);

  return {
    query,
    view,
    isEmpty: runs !== undefined && runs.length === 0,
    baseline: view?.comparison.baseline ?? "",
    setBaseline: setBaselineChoice,
  };
}
