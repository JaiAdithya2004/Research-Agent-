"use client";

import { AlertTriangle, Loader2, Undo2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useNow } from "@/hooks/use-now";
import { useReviewOutbox } from "@/hooks/use-review-outbox";
import { actionLabel, correctionLabel } from "@/lib/review-draft";
import type { PendingReview } from "@/lib/review-outbox";

function describe(e: PendingReview): string {
  const corrected = correctionLabel(e.request.corrected_value);
  return `${actionLabel(e.request.action)}${corrected ? ` (${corrected})` : ""}`;
}

/**
 * The undo toast and the outbox status. It lives in the root layout so a pending review keeps its
 * undo button when the user navigates. The polite live region announces each change once; the
 * visible seconds are aria-hidden so a countdown does not chatter.
 */
export function ReviewTray() {
  const outbox = useReviewOutbox();
  const waiting = outbox.entries.some((e) => e.status === "waiting");
  const now = useNow(waiting);

  return (
    <section
      aria-label="Review changes"
      className="pointer-events-none fixed inset-x-0 bottom-0 z-50 flex flex-col items-stretch gap-2 p-3 sm:inset-x-auto sm:right-4 sm:bottom-4 sm:w-96 sm:p-0"
    >
      <p role="status" aria-live="polite" className="sr-only" data-testid="review-announcer">
        {outbox.notice?.text ?? ""}
      </p>
      {outbox.entries.map((e) => {
        if (e.status === "failed") {
          return (
            <div
              key={e.id}
              role="alert"
              className="pointer-events-auto flex gap-3 rounded-lg border border-destructive/60 bg-card p-3 text-sm shadow-lg"
            >
              <AlertTriangle aria-hidden="true" className="mt-0.5 size-5 shrink-0 text-destructive" />
              <div className="min-w-0 flex-1 space-y-2">
                <p className="font-semibold">Not saved: {describe(e)}</p>
                <p className="break-words text-muted-foreground">
                  {e.label}. {e.error}
                </p>
                <div className="flex gap-2">
                  {e.retryable ? (
                    <Button size="sm" variant="outline" onClick={() => outbox.retry(e.id)}>
                      Retry
                    </Button>
                  ) : null}
                  <Button size="sm" variant="ghost" onClick={() => outbox.discard(e.id)}>
                    Dismiss
                  </Button>
                </div>
              </div>
            </div>
          );
        }
        if (e.status === "sending") {
          return (
            <div key={e.id} className="pointer-events-auto flex items-center gap-3 rounded-lg border bg-card p-3 text-sm shadow-lg">
              <Loader2 aria-hidden="true" className="size-4 shrink-0 motion-safe:animate-spin" />
              <p className="min-w-0 break-words">Saving {describe(e).toLowerCase()}…</p>
            </div>
          );
        }
        const secondsLeft = Math.max(0, Math.ceil((e.dueAt - now) / 1000));
        return (
          <div key={e.id} className="pointer-events-auto flex items-center gap-3 rounded-lg border bg-card p-3 text-sm shadow-lg">
            <div className="min-w-0 flex-1">
              <p className="font-semibold">{describe(e)} queued</p>
              <p className="truncate text-muted-foreground" title={e.label}>
                {e.label}
              </p>
              <p aria-hidden="true" className="text-xs text-muted-foreground">
                Saves in {secondsLeft}s
              </p>
            </div>
            <Button size="sm" variant="outline" onClick={() => outbox.undo(e.id)} aria-label={`Undo ${describe(e).toLowerCase()} for ${e.label}`}>
              <Undo2 aria-hidden="true" />
              Undo
            </Button>
          </div>
        );
      })}
    </section>
  );
}
