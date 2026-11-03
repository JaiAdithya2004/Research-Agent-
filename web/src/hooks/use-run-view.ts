"use client";

import { useQuery } from "@tanstack/react-query";
import { auditQueryOptions } from "@/hooks/use-audit";
import { pendingEntryFor, useReviewOutbox } from "@/hooks/use-review-outbox";
import { useRun } from "@/hooks/use-run";
import { ApiError } from "@/lib/api/errors";
import { presentVerdict } from "@/lib/verdict";

/** Everything the answer viewer shows, derived in one place. */
export function useRunView(runId: string) {
  const query = useRun(runId);
  const run = query.data;
  const audit = useQuery({ ...auditQueryOptions(run?.audit_id ?? ""), enabled: run !== undefined });
  const outbox = useReviewOutbox();
  // Only ask for the audit once the run says which audit it belongs to.
  const auditData = run ? audit.data : undefined;

  return {
    run,
    isPending: query.isPending,
    isNotFound: query.error instanceof ApiError && query.error.status === 404,
    error: query.isError ? query.error : null,
    retry: () => void query.refetch(),
    isRetrying: query.isRefetching,
    verdict: run ? presentVerdict(run) : null,
    pendingReview: run ? pendingEntryFor(outbox.entries, run.id) : undefined,
    auditCaps: auditData?.caps_hit ?? [],
    researchCapped: (auditData?.caps_hit ?? []).some((c) => c === "fetch_cap" || c === "domain_cap"),
    canReview: run?.status === "ok" && run.mention !== null,
  };
}
