"use client";

import { useQuery } from "@tanstack/react-query";
import { getApi } from "@/lib/api/client";
import { ApiError, unwrap } from "@/lib/api/errors";

export const runKey = (runId: string) => ["run", runId] as const;

export function useRun(runId: string) {
  return useQuery({
    queryKey: runKey(runId),
    queryFn: async ({ signal }) =>
      unwrap(await getApi().GET("/runs/{run_id}", { params: { path: { run_id: runId } }, signal }), "This answer"),
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 1,
  });
}
