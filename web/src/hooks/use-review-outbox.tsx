"use client";

import { createContext, useContext, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { sendReview, verifyReview } from "@/lib/review-api";
import { ReviewOutbox, type EnqueueInput, type OutboxSnapshot, type PendingReview } from "@/lib/review-outbox";

const TAB_KEY = "review-outbox:tab";
const EMPTY: OutboxSnapshot = { entries: [], notice: null };

function safeStorage(kind: "localStorage" | "sessionStorage"): Storage | null {
  try {
    return typeof window === "undefined" ? null : window[kind];
  } catch {
    return null;
  }
}

/** One id per browser tab, kept across reloads (sessionStorage), so a reload recognises its own sends. */
function tabId(): string {
  const session = safeStorage("sessionStorage");
  try {
    const existing = session?.getItem(TAB_KEY);
    if (existing) return existing;
    const fresh = crypto.randomUUID();
    session?.setItem(TAB_KEY, fresh);
    return fresh;
  } catch {
    return crypto.randomUUID();
  }
}

export interface ReviewOutboxApi {
  entries: readonly PendingReview[];
  notice: OutboxSnapshot["notice"];
  enqueue: (input: EnqueueInput) => string;
  undo: (id: string) => boolean;
  retry: (id: string) => void;
  discard: (id: string) => void;
}

const noop: ReviewOutboxApi = {
  entries: [],
  notice: null,
  enqueue: () => "",
  undo: () => false,
  retry: () => undefined,
  discard: () => undefined,
};

const Context = createContext<ReviewOutboxApi>(noop);

export function ReviewOutboxProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const [outbox] = useState(
    () =>
      new ReviewOutbox({
        storage: safeStorage("localStorage"),
        now: () => Date.now(),
        newId: () => crypto.randomUUID(),
        tabId: tabId(),
        send: sendReview,
        verify: verifyReview,
        onSettled: () => {
          // The review changed the audit's numbers, the queue and the run.
          for (const key of ["review", "run", "audit", "audits"]) void queryClient.invalidateQueries({ queryKey: [key] });
        },
      }),
  );

  useEffect(() => {
    outbox.start();
    return () => outbox.stop();
  }, [outbox]);

  const snapshot = useSyncExternalStore(outbox.subscribe, outbox.getSnapshot, () => EMPTY);
  const api = useMemo<ReviewOutboxApi>(
    () => ({
      entries: snapshot.entries,
      notice: snapshot.notice,
      enqueue: (input) => outbox.enqueue(input),
      undo: (id) => outbox.undo(id),
      retry: (id) => outbox.retry(id),
      discard: (id) => outbox.discard(id),
    }),
    [snapshot, outbox],
  );
  return <Context.Provider value={api}>{children}</Context.Provider>;
}

export function useReviewOutbox(): ReviewOutboxApi {
  return useContext(Context);
}

/** The outbox entry for a run that is still on its way to the server (the optimistic state). */
export function pendingEntryFor(entries: readonly PendingReview[], runId: string): PendingReview | undefined {
  return entries.find((e) => e.runId === runId && (e.status !== "failed" || e.retryable));
}

