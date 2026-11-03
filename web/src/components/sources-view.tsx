"use client";

import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { AuditBanners } from "@/components/audit-banners";
import { GapReport } from "@/components/gap-report";
import { PageHeader } from "@/components/page-header";
import { SourceDetail } from "@/components/source-detail";
import { SourceFilters, SourcesTable } from "@/components/sources-table";
import { StatusChip } from "@/components/status-chip";
import { buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/view-states";
import { useAuditsList } from "@/hooks/use-audits-list";
import { useSourcesPage } from "@/hooks/use-sources-page";
import { NO_FILTERS } from "@/lib/audit-filters";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

/** No audit chosen yet: pick one. The sources and gaps belong to one audit each. */
function AuditPicker() {
  const list = useAuditsList(NO_FILTERS);
  if (list.isPending) {
    return (
      <div role="status" aria-label="Loading audits" className="space-y-2">
        <span className="sr-only">Loading audits</span>
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-12" />
        ))}
      </div>
    );
  }
  if (list.isError && list.items.length === 0) {
    return <ErrorState title="Could not load audits" message={list.error.message} onRetry={() => void list.refetch()} />;
  }
  if (list.items.length === 0) {
    return (
      <EmptyState
        title="No audits yet"
        description="Sources belong to an audit. Create one first."
        action={<Link href="/audits/new" className={cn(buttonVariants())}>Create an audit</Link>}
      />
    );
  }
  return (
    <section aria-labelledby="pick-heading">
      <h2 id="pick-heading" className="mb-2 text-lg font-semibold">Choose an audit</h2>
      <ul className="divide-y rounded-lg border">
        {list.items.map((a) => (
          <li key={a.id}>
            <Link href={`/sources?audit_id=${encodeURIComponent(a.id)}`} className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 hover:bg-accent">
              <span className="font-medium">{a.brand}</span>
              <span className="flex items-center gap-2 text-sm text-muted-foreground">
                <StatusChip status={a.status} />
                <time dateTime={a.created_at}>{formatDateTime(a.created_at)}</time>
              </span>
            </Link>
          </li>
        ))}
      </ul>
      {list.hasNextPage ? (
        <p className="mt-2 text-sm text-muted-foreground">Showing the newest {list.items.length} audits. Use the Audits page to find older ones.</p>
      ) : null}
    </section>
  );
}

export function SourcesView() {
  const s = useSourcesPage();

  if (s.auditId === null) {
    return (
      <>
        <PageHeader title="Sources" description="Researched pages and the source gap report." />
        <AuditPicker />
      </>
    );
  }

  if (s.auditNotFound) {
    return (
      <>
        <PageHeader title="Sources" />
        <EmptyState
          title="Audit not found"
          description="There is no audit with this id."
          action={<Link href="/sources" className={cn(buttonVariants({ variant: "outline" }))}>Choose another audit</Link>}
        />
      </>
    );
  }

  const audit = s.audit.data;
  const hitFetchCap = (audit?.caps_hit ?? []).some((c) => c === "fetch_cap" || c === "domain_cap");

  return (
    <>
      <Link href="/sources" className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "mb-3 -ml-3")}>
        <ArrowLeft aria-hidden="true" />
        All audits
      </Link>
      <PageHeader
        title={audit ? `Sources for ${audit.brand}` : "Sources"}
        description="Pages cited in the answers, what was found on them, and where competitors are cited but you are not."
        actions={
          <Link href={`/audits/${encodeURIComponent(s.auditId)}`} className={cn(buttonVariants({ variant: "outline" }))}>
            Open the audit
          </Link>
        }
      />
      {s.audit.isError && !s.auditNotFound ? (
        <div className="mb-4">
          <ErrorState
            title="Could not load the audit's status"
            message={`${s.audit.error.message} The notices about caps and degraded runs are missing, so treat the lists below as possibly partial.`}
            onRetry={() => void s.audit.refetch()}
          />
        </div>
      ) : null}
      <AuditBanners banners={s.banners} />

      {s.params.sourceId ? (
        <SourceDetail
          data={s.detail.query.data}
          freshness={s.detail.freshness}
          isPending={s.detail.query.isPending}
          notFound={s.detail.notFound}
          error={s.detail.query.isError && !s.detail.notFound ? s.detail.query.error : null}
          onRetry={() => void s.detail.query.refetch()}
          onClose={s.closeDetail}
        />
      ) : null}

      <GapReport
        params={s.params}
        rows={s.gaps.rows}
        total={s.gaps.total}
        hidden={s.gaps.hidden}
        showAll={s.gaps.showAll}
        onToggleAll={s.gaps.toggleAll}
        isPending={s.gaps.query.isPending}
        error={s.gaps.query.isError ? s.gaps.query.error : null}
        onRetry={() => void s.gaps.query.refetch()}
        auditRunning={audit?.status === "running"}
      />

      <section aria-labelledby="sources-heading">
        <h2 id="sources-heading" className="mb-2 text-lg font-semibold">All sources</h2>
        <SourceFilters
          params={s.params}
          domainInput={s.domainInput}
          onDomainInput={s.setDomainInput}
          onApplyDomain={s.applyDomain}
          onType={s.setType}
          onStatus={s.setStatus}
          onClear={s.clearFilters}
          filtered={s.filtered}
        />
        <div aria-live="polite" className="sr-only" data-testid="sources-live-region">
          {s.sources.query.isSuccess ? `${s.sources.summary.total} sources shown` : ""}
        </div>
        <SourcesTable
          params={s.params}
          rows={s.sources.rows}
          hidden={s.sources.hidden}
          summary={s.sources.summary}
          showAll={s.sources.showAll}
          onToggleAll={s.sources.toggleAll}
          isPending={s.sources.query.isPending}
          error={s.sources.query.isError ? s.sources.query.error : null}
          onRetry={() => void s.sources.query.refetch()}
          filtered={s.filtered}
          auditHitFetchCap={hitFetchCap}
        />
      </section>
    </>
  );
}
