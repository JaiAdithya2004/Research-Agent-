"use client";

import Link from "next/link";
import { ArrowLeft, Hourglass } from "lucide-react";
import { AnswerText } from "@/components/answer-text";
import { CitationsPanel } from "@/components/citations-panel";
import { ReviewForm } from "@/components/review-form";
import { Alert } from "@/components/ui/alert";
import { buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/view-states";
import { VerdictChip } from "@/components/verdict-chip";
import { useRunView } from "@/hooks/use-run-view";
import { actionLabel, correctionLabel } from "@/lib/review-draft";
import { modeLabel } from "@/lib/stability";
import { cn } from "@/lib/utils";

const SENTIMENT = { pos: "Positive", neu: "Neutral", neg: "Negative" } as const;

function BackLink({ auditId }: { auditId: string }) {
  return (
    <Link href={`/audits/${auditId}`} className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "mb-3 -ml-3")}>
      <ArrowLeft aria-hidden="true" />
      Back to the audit
    </Link>
  );
}

export function RunViewerSkeleton() {
  return (
    <div role="status" aria-label="Loading answer" className="space-y-4">
      <span className="sr-only">Loading answer</span>
      <Skeleton className="h-8 w-80" />
      <Skeleton className="h-40" />
      <Skeleton className="h-32" />
    </div>
  );
}

export function RunViewer({ auditId, runId }: { auditId: string; runId: string }) {
  const v = useRunView(runId);

  if (v.isPending) {
    return (
      <>
        <BackLink auditId={auditId} />
        <RunViewerSkeleton />
      </>
    );
  }
  if (v.isNotFound) {
    return (
      <>
        <BackLink auditId={auditId} />
        <div className="rounded-lg border border-dashed px-6 py-12 text-center">
          <h1 className="text-xl font-semibold">Answer not found</h1>
          <p className="mt-2 text-sm text-muted-foreground">There is no run with this id.</p>
        </div>
      </>
    );
  }
  if (v.error || !v.run || !v.verdict) {
    return (
      <>
        <BackLink auditId={auditId} />
        <h1 className="sr-only">Answer</h1>
        <ErrorState
          title="Could not load this answer"
          message={v.error?.message ?? "Try again."}
          onRetry={v.retry}
          retrying={v.isRetrying}
        />
      </>
    );
  }

  const { run, verdict } = v;
  const mention = run.mention;
  const quote = mention?.quote ?? null;
  const effective =
    run.brand_mentioned === null ? "No usable outcome" : run.brand_mentioned ? "Brand counted as mentioned" : "Brand counted as not mentioned";

  return (
    <>
      <BackLink auditId={run.audit_id} />
      <header className="mb-4 space-y-2">
        <h1 className="text-xl font-semibold tracking-tight break-words">{run.question}</h1>
        <p className="text-sm text-muted-foreground">
          {run.provider} / {run.model}, {modeLabel(run.search_mode)}, repeat{" "}
          {run.repeat_idx + 1}.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <VerdictChip verdict={verdict} />
          <span className="text-sm font-medium">{effective}</span>
          {v.pendingReview ? (
            <span
              role="status"
              className="inline-flex items-center gap-1 rounded-full border border-dashed border-info/60 bg-info-bg px-2.5 py-0.5 text-sm font-semibold text-info"
            >
              <Hourglass aria-hidden="true" className="size-4" />
              {actionLabel(v.pendingReview.request.action)} pending
              {v.pendingReview.status === "waiting" ? " (undo available)" : ""}
            </span>
          ) : null}
        </div>
        {verdict.detail ? <p className="text-sm text-muted-foreground break-words">{verdict.detail}</p> : null}
      </header>

      <div className="space-y-4">
        {run.degraded ? (
          <Alert tone="warning" title="Degraded run">
            <p>Search was unavailable for this run, so it answered without search. It has no citations to check.</p>
          </Alert>
        ) : null}

        {run.status === "failed" ? (
          <Alert tone="danger" title="This run produced no usable answer" role="status">
            <p className="break-words">{run.error ?? "The run failed."}</p>
            <p>It is not counted as a mention or as a miss.</p>
          </Alert>
        ) : null}
        {run.status === "pending" ? (
          <Alert tone="info" title="This run has not finished yet">
            <p>The answer and its citations appear when the run completes. Reload to check again.</p>
          </Alert>
        ) : null}
        {v.researchCapped ? (
          <Alert tone="warning" title="This audit stopped fetching pages at a limit">
            <p>The results are partial: some cited pages were never checked.</p>
          </Alert>
        ) : null}

        {run.status === "ok" ? (
          <section aria-labelledby="answer-heading" className="rounded-lg border p-4">
            <h2 id="answer-heading" className="mb-2 text-lg font-semibold">
              Raw answer
            </h2>
            <p className="mb-2 text-sm text-muted-foreground">
              The model&apos;s answer exactly as it was received. The highlighted part is the quote the extractor used.
            </p>
            <AnswerText text={run.raw_answer} spans={run.highlights} quote={quote} />
          </section>
        ) : null}

        {mention ? (
          <section aria-labelledby="mention-heading" className="rounded-lg border p-4">
            <h2 id="mention-heading" className="mb-2 text-lg font-semibold">
              What was extracted
            </h2>
            <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
              <div>
                <dt className="font-medium">Brand mentioned</dt>
                <dd>{mention.brand_mentioned ? "Yes" : "No"}</dd>
              </div>
              <div>
                <dt className="font-medium">Position</dt>
                <dd>{mention.position ?? "Not ranked"}</dd>
              </div>
              <div>
                <dt className="font-medium">Sentiment</dt>
                <dd>{SENTIMENT[mention.sentiment]}</dd>
              </div>
              <div>
                <dt className="font-medium">Competitors seen</dt>
                <dd className="break-words">
                  {(mention.competitors_seen ?? []).length > 0 ? mention.competitors_seen!.join(", ") : "None"}
                </dd>
              </div>
            </dl>
            {run.findings.some((f) => f.status === "flagged") ? (
              <>
                <h3 className="mt-4 text-sm font-semibold">Why this needs a check</h3>
                <ul className="mt-1 list-disc space-y-1 pl-5 text-sm">
                  {run.findings
                    .filter((f) => f.status === "flagged")
                    .map((f, i) => (
                      <li key={`${f.code}-${i}`} className="break-words">
                        {f.reason}
                      </li>
                    ))}
                </ul>
              </>
            ) : null}
          </section>
        ) : null}

        {run.status === "ok" ? (
          <CitationsPanel
            citations={run.citations}
            auditId={run.audit_id}
            searchMode={run.search_mode}
            researchCapped={v.researchCapped}
          />
        ) : null}

        {v.canReview ? (
          <section aria-labelledby="review-heading" className="rounded-lg border p-4">
            <h2 id="review-heading" className="mb-2 text-lg font-semibold">
              Human review
            </h2>
            {run.review ? (
              <p className="mb-3 text-sm text-muted-foreground">
                Latest review: {actionLabel(run.review.action)}
                {run.review.corrected_value ? ` (${correctionLabel(run.review.corrected_value)})` : ""} by{" "}
                {run.review.reviewer}. Reviews are never overwritten: a new one is added and the latest counts.
              </p>
            ) : null}
            <details open={verdict.kind === "flagged"}>
              <summary className="min-h-10 cursor-pointer py-2 text-sm font-medium focus-visible:outline-2 focus-visible:outline-ring">
                {run.review ? "Add a new review" : "Review this mention"}
              </summary>
              <div className="mt-3">
                <ReviewForm runId={run.id} label={run.question} />
              </div>
            </details>
          </section>
        ) : null}
      </div>
    </>
  );
}
