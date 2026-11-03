"use client";

import { useQuery } from "@tanstack/react-query";
import { getApi } from "@/lib/api/client";
import { ApiError, unwrap } from "@/lib/api/errors";
import type { SourceFetchStatus, SourceType } from "@/lib/source-filters";

const retryUnlessMissing = (count: number, error: Error) =>
  !(error instanceof ApiError && error.status === 404) && count < 1;

export function useGaps(auditId: string | null) {
  return useQuery({
    queryKey: ["audit", auditId, "gaps"],
    enabled: auditId !== null,
    retry: retryUnlessMissing,
    queryFn: async ({ signal }) =>
      unwrap(await getApi().GET("/audits/{audit_id}/gaps", { params: { path: { audit_id: auditId! } }, signal }), "The gap report"),
  });
}

export interface SourceQuery {
  sourceType: SourceType | null;
  fetchStatus: SourceFetchStatus | null;
  domain: string;
}

export function useSources(auditId: string | null, q: SourceQuery) {
  return useQuery({
    queryKey: ["audit", auditId, "sources", q.sourceType, q.fetchStatus, q.domain],
    enabled: auditId !== null,
    retry: retryUnlessMissing,
    queryFn: async ({ signal }) =>
      unwrap(
        await getApi().GET("/audits/{audit_id}/sources", {
          params: {
            path: { audit_id: auditId! },
            query: {
              source_type: q.sourceType ?? undefined,
              fetch_status: q.fetchStatus ?? undefined,
              domain: q.domain || undefined,
            },
          },
          signal,
        }),
        "The sources",
      ),
  });
}

export function useSource(auditId: string | null, sourceId: string | null) {
  return useQuery({
    queryKey: ["audit", auditId, "source", sourceId],
    enabled: auditId !== null && sourceId !== null,
    retry: retryUnlessMissing,
    queryFn: async ({ signal }) =>
      unwrap(
        await getApi().GET("/audits/{audit_id}/sources/{source_id}", {
          params: { path: { audit_id: auditId!, source_id: sourceId! } },
          signal,
        }),
        "This source",
      ),
  });
}
