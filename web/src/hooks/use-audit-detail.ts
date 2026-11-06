"use client";

import { useMemo } from "react";
import { useAudit, useAuditRuns } from "@/hooks/use-audit";
import { useAuditEvents } from "@/hooks/use-audit-events";
import { ApiError } from "@/lib/api/errors";
import { buildBanners } from "@/lib/audit-banners";
import { displayedConnection } from "@/lib/audit-events";
import { announcementText, progressView } from "@/lib/audit-progress";
import { modeDeltaView, reportHeadline, scorecardMetrics } from "@/lib/scorecard";
import { buildQuestionRows } from "@/lib/stability";

/** Everything the audit detail page shows, derived in one place so the components only render. */
export function useAuditDetail(auditId: string) {
  const auditQuery = useAudit(auditId);
  const runsQuery = useAuditRuns(auditId);
  const audit = auditQuery.data;
  const running = audit?.status === "running";
  const events = useAuditEvents(auditId, { enabled: running });

  const runs = runsQuery.data;
  const view = useMemo(() => {
    if (!audit) return null;
    const finishedRows = runs?.filter((r) => r.status !== "pending").length ?? 0;
    const progress = progressView(events.progress, finishedRows);
    return {
      audit,
      metrics: scorecardMetrics(audit),
      modeDelta: modeDeltaView(audit.summary),
      headline: reportHeadline(audit),
      banners: buildBanners(audit, runs),
      rows: buildQuestionRows(audit.request.questions, audit.summary?.question_stability ?? [], runs ?? [], {
        repeats: audit.request.repeats,
        running: audit.status === "running",
      }),
      progress,
      announcement: announcementText(progress.done, progress.planned),
      hasAnyAnswers: (audit.summary?.question_stability?.length ?? 0) > 0 || (runs?.length ?? 0) > 0,
    };
  }, [audit, runs, events.progress]);

  return {
    view,
    isPending: auditQuery.isPending,
    isNotFound: auditQuery.error instanceof ApiError && auditQuery.error.status === 404,
    error: auditQuery.isError ? auditQuery.error : null,
    retry: () => void auditQuery.refetch(),
    isRetrying: auditQuery.isRefetching,
    connection: displayedConnection(audit?.status, events.connection),
    runsLoading: runsQuery.isPending,
    runsError: runsQuery.isError ? runsQuery.error : null,
    retryRuns: () => void runsQuery.refetch(),
  };
}
