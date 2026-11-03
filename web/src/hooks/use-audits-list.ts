"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { getApi } from "@/lib/api/client";
import { unwrap } from "@/lib/api/errors";
import type { AuditFilters } from "@/lib/audit-filters";

export const PAGE_SIZE = 20;

/** Cursor-paginated audits. Filters are part of the query key, so each combination is cached. */
export function useAuditsList(filters: AuditFilters) {
  const query = useInfiniteQuery({
    queryKey: ["audits", "list", filters.status, filters.brand],
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam, signal }) =>
      unwrap(
        await getApi().GET("/audits", {
          params: {
            query: {
              limit: PAGE_SIZE,
              cursor: pageParam,
              status: filters.status ?? undefined,
              brand: filters.brand || undefined,
            },
          },
          signal,
        }),
        "The audits list",
      ),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  const items = query.data?.pages.flatMap((p) => p.items) ?? [];
  // Computed here so the page can show a "could not load more" error next to the rows already loaded.
  const nextPageError = query.isFetchNextPageError ? query.error : null;
  return { ...query, items, nextPageError };
}
