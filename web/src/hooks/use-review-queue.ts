"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { useReviewOutbox, pendingEntryFor } from "@/hooks/use-review-outbox";
import { getApi } from "@/lib/api/client";
import { unwrap } from "@/lib/api/errors";

export const QUEUE_PAGE_SIZE = 20;

/**
 * The flagged mentions waiting for a human. Items with a decision on its way (inside the undo
 * window, sending, or failed but retryable) are hidden: that is the optimistic update. Undo, or a
 * refused review, makes the item reappear.
 */
export function useReviewQueue(auditId: string | null) {
  const outbox = useReviewOutbox();
  const query = useInfiniteQuery({
    queryKey: ["review", "queue", auditId],
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam, signal }) =>
      unwrap(
        await getApi().GET("/review/queue", {
          params: { query: { audit_id: auditId ?? undefined, limit: QUEUE_PAGE_SIZE, cursor: pageParam } },
          signal,
        }),
        "The review queue",
      ),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });

  const loaded = query.data?.pages.flatMap((p) => p.items) ?? [];
  const items = loaded.filter((item) => pendingEntryFor(outbox.entries, item.run_id) === undefined);
  const refused = outbox.entries.filter((e) => e.status === "failed" && !e.retryable);
  return {
    ...query,
    items,
    hiddenPending: loaded.length - items.length,
    refused,
    nextPageError: query.isFetchNextPageError ? query.error : null,
  };
}
