"use client";

import { queryOptions, useQuery } from "@tanstack/react-query";
import { getApi } from "@/lib/api/client";
import { ApiError, unwrap } from "@/lib/api/errors";

export const auditKey = (auditId: string) => ["audit", auditId] as const;
export const runsKey = (auditId: string) => ["audit", auditId, "runs"] as const;

/** A missing audit will not appear on retry; other failures get one retry. */
const retryUnlessMissing = (count: number, error: Error) =>
  !(error instanceof ApiError && error.status === 404) && count < 1;

export function auditQueryOptions(auditId: string) {
  return queryOptions({
    queryKey: auditKey(auditId),
    queryFn: async ({ signal }) =>
      unwrap(await getApi().GET("/audits/{audit_id}", { params: { path: { audit_id: auditId } }, signal }), "This audit"),
    retry: retryUnlessMissing,
  });
}

export function useAudit(auditId: string) {
  return useQuery(auditQueryOptions(auditId));
}

export function useAuditRuns(auditId: string) {
  return useQuery({
    queryKey: runsKey(auditId),
    queryFn: async ({ signal }) =>
      unwrap(
        await getApi().GET("/audits/{audit_id}/runs", { params: { path: { audit_id: auditId } }, signal }),
        "The runs of this audit",
      ),
    retry: retryUnlessMissing,
  });
}
