"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, FileText } from "lucide-react";
import { AuditBanners } from "@/components/audit-banners";
import { DeleteAuditButton } from "@/components/delete-audit-button";
import { ConnectionBadge, ProgressPanel } from "@/components/progress-panel";
import { DotLegend, QuestionStabilityList } from "@/components/question-stability-list";
import { ModeDeltaCard, Scorecard } from "@/components/scorecard";
import { CapsChips, DegradedChip, StatusChip } from "@/components/status-chip";
import { buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/view-states";
import { useAuditDetail } from "@/hooks/use-audit-detail";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

const RATING_TONE = {
  success: "border-success/50 bg-success-bg text-success",
  warning: "border-warning/60 bg-warning-bg text-warning",
  danger: "border-destructive/50 bg-destructive-bg text-destructive",
} as const;

const BackLink = () => (
  <Link href="/" className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "mb-3 -ml-3")}>
    <ArrowLeft aria-hidden="true" />
    All audits
  </Link>
);

export function AuditDetailSkeleton() {
  return (
    <div role="status" aria-label="Loading audit" className="space-y-4">
      <span className="sr-only">Loading audit</span>
      <Skeleton className="h-8 w-64" />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {Array.from({ length: 6 }, (_, i) => (
          <Skeleton key={i} className="h-20" />
        ))}
      </div>
      <Skeleton className="h-32" />
      <Skeleton className="h-32" />
    </div>
  );
}

export function AuditDetailView({ auditId }: { auditId: string }) {
  const d = useAuditDetail(auditId);
  const router = useRouter();

  if (d.isPending) {
    return (
      <>
        <BackLink />
        <AuditDetailSkeleton />
      </>
    );
  }
  if (d.isNotFound) {
    return (
      <>
        <BackLink />
        <div className="rounded-lg border border-dashed px-6 py-12 text-center">
          <h1 className="text-xl font-semibold">Audit not found</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            There is no audit with this id. It may have been mistyped, or it belongs to another API key.
          </p>
          <Link href="/" className={cn(buttonVariants(), "mt-4")}>
            Back to all audits
          </Link>
        </div>
      </>
    );
  }
  if (d.error || !d.view) {
    return (
      <>
        <BackLink />
        <h1 className="sr-only">Audit</h1>
        <ErrorState
          title="Could not load this audit"
          message={d.error?.message ?? "Try again."}
          onRetry={d.retry}
          retrying={d.isRetrying}
        />
      </>
    );
  }

  const { audit, metrics, modeDelta, banners, rows, progress, announcement, hasAnyAnswers, headline, rating } = d.view;
  const running = audit.status === "running";

  return (
    <>
      <BackLink />
      <header className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{audit.brand}</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Started <time dateTime={audit.created_at}>{formatDateTime(audit.created_at)}</time>
            {audit.completed_at ? (
              <>
                , finished <time dateTime={audit.completed_at}>{formatDateTime(audit.completed_at)}</time>
              </>
            ) : null}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <StatusChip status={audit.status} />
          {audit.degraded ? <DegradedChip /> : null}
          <CapsChips caps={audit.caps_hit} />
          <ConnectionBadge state={d.connection} />
        </div>
        <DeleteAuditButton
          auditId={audit.id}
          brand={audit.brand}
          disabled={running}
          onDeleted={() => router.push("/")}
        />
      </header>

      <AuditBanners banners={banners} />

      {running ? <ProgressPanel view={progress} announcement={announcement} /> : null}

      <Scorecard metrics={metrics} />
      {modeDelta ? <ModeDeltaCard view={modeDelta} /> : null}

      {headline ? (
        <section aria-labelledby="report-heading" className="mb-6 rounded-lg border bg-card p-4">
          <h2 id="report-heading" className="flex items-center gap-2 text-lg font-semibold">
            <FileText aria-hidden="true" className="size-5" />
            Audit report
          </h2>
          {rating ? (
            <p className="mt-3 flex flex-wrap items-center gap-2">
              <span className={cn("rounded-md border px-2.5 py-1 text-sm font-semibold", RATING_TONE[rating.tone])}>{rating.label}</span>
              <span className="text-sm">{rating.sentence}</span>
            </p>
          ) : null}
          <p className="mt-2 text-sm text-muted-foreground">{headline}</p>
          <p className="mt-1 text-sm text-muted-foreground">
            See which websites the AI cited for your competitors but not for you, and what each page says about your brand.
          </p>
          <Link
            href={`/sources?audit_id=${encodeURIComponent(audit.id)}`}
            className={cn(buttonVariants({ variant: "outline" }), "mt-3")}
          >
            Sources and gap report
          </Link>
        </section>
      ) : null}

      <section aria-labelledby="questions-heading">
        <h2 id="questions-heading" className="mb-3 text-lg font-semibold">
          Questions
        </h2>
        {d.runsError ? (
          <div className="mb-3">
            <ErrorState
              title="Could not load the individual runs"
              message={`${d.runsError.message} The marks below still show the results, but they cannot link to each answer yet.`}
              onRetry={d.retryRuns}
            />
          </div>
        ) : null}
        {running && !hasAnyAnswers ? (
          <p role="status" className="rounded-lg border border-dashed px-6 py-10 text-center text-sm text-muted-foreground">
            Waiting for the first answers. Results appear here as each run finishes.
          </p>
        ) : (
          <>
            <DotLegend />
            {d.runsLoading ? <span className="sr-only">Loading links to each answer</span> : null}
            <QuestionStabilityList rows={rows} auditId={audit.id} brand={audit.brand} />
          </>
        )}
      </section>
    </>
  );
}
