"use client";

import Link from "next/link";
import { useRef } from "react";
import { Flag } from "lucide-react";
import { AnswerText } from "@/components/answer-text";
import { PageHeader } from "@/components/page-header";
import { ReviewForm } from "@/components/review-form";
import { Alert } from "@/components/ui/alert";
import { Button, buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/view-states";
import { useReviewQueue } from "@/hooks/use-review-queue";
import type { components } from "@/lib/api/schema.gen";
import { pluralize } from "@/lib/format";
import { modeLabel } from "@/lib/stability";
import { cn } from "@/lib/utils";

type Item = components["schemas"]["QueueItem"];

export function ReviewQueueSkeleton() {
  return (
    <div role="status" aria-label="Loading the review queue" className="space-y-4">
      <span className="sr-only">Loading the review queue</span>
      {[0, 1].map((i) => (
        <Skeleton key={i} className="h-64" />
      ))}
    </div>
  );
}

function QueueCard({ item, onQueued }: { item: Item; onQueued: () => void }) {
  return (
    <li>
      <article aria-labelledby={`q-${item.run_id}`} className="space-y-3 rounded-lg border p-4">
        <header className="space-y-1">
          <h2 id={`q-${item.run_id}`} className="text-base font-semibold break-words">
            {item.question}
          </h2>
          <p className="text-sm text-muted-foreground">
            {item.brand}, {item.provider} / {item.model}, {modeLabel(item.search_mode)},
            repeat {item.repeat_idx + 1}
          </p>
        </header>
        <Alert tone="warning" title="Why this was flagged">
          <p className="break-words">{item.verdict_reason || "No reason was recorded."}</p>
        </Alert>
        <p className="text-sm">
          The extractor said the brand was <strong>{item.brand_mentioned ? "mentioned" : "not mentioned"}</strong>
          {item.position !== null ? <>, at position {item.position}</> : null}.
        </p>
        <AnswerText text={item.raw_answer} spans={item.highlights} quote={item.quote} label={`Raw answer for: ${item.question}`} />
        <ReviewForm runId={item.run_id} label={item.question} onQueued={onQueued} />
        <Link
          href={`/audits/${item.audit_id}/runs/${item.run_id}`}
          className="inline-block text-sm underline underline-offset-4"
        >
          Open the full answer and citations
        </Link>
      </article>
    </li>
  );
}

export function ReviewQueueView({ auditId }: { auditId: string | null }) {
  const q = useReviewQueue(auditId);
  const listRef = useRef<HTMLHeadingElement>(null);
  const initialError = q.isError && !q.nextPageError;

  return (
    <>
      <PageHeader
        title="Review"
        description="Flagged mentions waiting for a human decision. Each decision needs a reason and can be undone for 5 seconds."
      />
      {auditId ? (
        <p className="mb-4 text-sm">
          Showing flagged mentions for one audit.{" "}
          <Link href="/review" className="underline underline-offset-4">
            Show every audit
          </Link>{" "}
          or{" "}
          <Link href={`/audits/${auditId}`} className="underline underline-offset-4">
            open the audit
          </Link>
          .
        </p>
      ) : null}

      {q.refused.length > 0 ? (
        <div className="mb-4">
          <Alert tone="danger" title="Some decisions were refused by the server" role="alert">
            <p>
              {pluralize(q.refused.length, "decision")} could not be saved, so the item is back in the queue. The
              message is in the notice at the bottom of the screen.
            </p>
          </Alert>
        </div>
      ) : null}

      <h2 ref={listRef} tabIndex={-1} className="sr-only">
        Flagged mentions
      </h2>
      <div aria-live="polite" className="sr-only" data-testid="queue-live-region">
        {q.isSuccess ? `${pluralize(q.items.length, "flagged mention")} waiting${q.hasNextPage ? ", more available" : ""}` : ""}
      </div>

      {q.isPending ? (
        <ReviewQueueSkeleton />
      ) : initialError ? (
        <ErrorState
          title="Could not load the review queue"
          message={q.error?.message ?? "Try again."}
          onRetry={() => void q.refetch()}
          retrying={q.isRefetching}
        />
      ) : q.items.length === 0 && !q.hasNextPage ? (
        q.hiddenPending > 0 ? (
          <EmptyState
            title="All caught up for now"
            description={`${pluralize(q.hiddenPending, "decision")} on the way to being saved. Undo is available in the notice at the bottom of the screen.`}
          />
        ) : (
          <EmptyState
            title="Nothing waiting for review"
            description="No flagged mentions need a human decision right now. Flagged items appear here when an audit's verifier cannot confirm a quote."
            action={
              <Link href="/" className={cn(buttonVariants({ variant: "outline" }))}>
                Back to audits
              </Link>
            }
          />
        )
      ) : (
        <>
          <p className="mb-3 flex items-center gap-2 text-sm text-muted-foreground">
            <Flag aria-hidden="true" className="size-4" />
            {pluralize(q.items.length, "flagged mention")} loaded
            {q.hiddenPending > 0 ? `, ${q.hiddenPending} hidden while being saved` : ""}.
          </p>
          <ul className="space-y-4">
            {q.items.map((item) => (
              <QueueCard key={item.run_id} item={item} onQueued={() => listRef.current?.focus()} />
            ))}
          </ul>
          {q.nextPageError ? (
            <div className="mt-4">
              <ErrorState
                title="Could not load more"
                message={`${q.nextPageError.message} The items above are still current.`}
                onRetry={() => void q.fetchNextPage()}
                retrying={q.isFetchingNextPage}
              />
            </div>
          ) : q.hasNextPage ? (
            <div className="mt-4 text-center">
              <p className="mb-2 text-sm text-muted-foreground">
                Showing the first {q.items.length}. More flagged mentions are waiting.
              </p>
              <Button variant="outline" onClick={() => void q.fetchNextPage()} disabled={q.isFetchingNextPage}>
                {q.isFetchingNextPage ? "Loading…" : "Load more"}
              </Button>
            </div>
          ) : (
            <p className="mt-4 text-center text-sm text-muted-foreground">That is every flagged mention.</p>
          )}
        </>
      )}
    </>
  );
}
