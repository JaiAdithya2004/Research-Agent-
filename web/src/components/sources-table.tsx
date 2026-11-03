import Link from "next/link";
import { EvidenceBadge, FetchStatusChip } from "@/components/evidence-badge";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/view-states";
import type { components } from "@/lib/api/schema.gen";
import { FETCH_STATUS_LABEL } from "@/lib/evidence";
import { formatDateTime, pluralize } from "@/lib/format";
import { safeHref } from "@/lib/safe-url";
import type { SourcesSummary } from "@/lib/sources";
import {
  FETCH_STATUSES,
  MAX_DOMAIN_FILTER,
  SOURCE_TYPES,
  SOURCE_TYPE_LABEL,
  serializeSourcesParams,
  type SourceFetchStatus,
  type SourceType,
  type SourcesParams,
} from "@/lib/source-filters";

type Source = components["schemas"]["SourceOut"];

export function SourcesTableSkeleton() {
  return (
    <div role="status" aria-label="Loading sources" className="space-y-2">
      <span className="sr-only">Loading sources</span>
      {[0, 1, 2, 3].map((i) => (
        <Skeleton key={i} className="h-10" />
      ))}
    </div>
  );
}

export function SourceFilters({
  params,
  domainInput,
  onDomainInput,
  onApplyDomain,
  onType,
  onStatus,
  onClear,
  filtered,
}: {
  params: SourcesParams;
  domainInput: string;
  onDomainInput: (v: string) => void;
  onApplyDomain: () => void;
  onType: (t: SourceType | null) => void;
  onStatus: (s: SourceFetchStatus | null) => void;
  onClear: () => void;
  filtered: boolean;
}) {
  return (
    <form
      role="search"
      aria-label="Filter sources"
      className="mb-3 flex flex-wrap items-end gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        onApplyDomain();
      }}
    >
      <div className="flex flex-col gap-1">
        <label htmlFor="filter-type" className="text-sm font-medium">Type</label>
        <Select id="filter-type" value={params.sourceType ?? ""} onChange={(e) => onType((e.target.value || null) as SourceType | null)}>
          <option value="">All types</option>
          {SOURCE_TYPES.map((t) => (
            <option key={t} value={t}>{SOURCE_TYPE_LABEL[t]}</option>
          ))}
        </Select>
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor="filter-fetch" className="text-sm font-medium">Fetch status</label>
        <Select id="filter-fetch" value={params.fetchStatus ?? ""} onChange={(e) => onStatus((e.target.value || null) as SourceFetchStatus | null)}>
          <option value="">All statuses</option>
          {FETCH_STATUSES.map((s) => (
            <option key={s} value={s}>{FETCH_STATUS_LABEL[s]}</option>
          ))}
        </Select>
      </div>
      <div className="flex min-w-48 flex-1 flex-col gap-1">
        <label htmlFor="filter-domain" className="text-sm font-medium">Domain (exact, press Enter)</label>
        <Input
          id="filter-domain"
          type="search"
          value={domainInput}
          maxLength={MAX_DOMAIN_FILTER}
          placeholder="example.com"
          onChange={(e) => onDomainInput(e.target.value)}
        />
      </div>
      <Button type="submit" variant="outline">Apply</Button>
      {filtered ? (
        <Button variant="ghost" onClick={onClear}>Clear filters</Button>
      ) : null}
    </form>
  );
}

export function SourcesTable({
  params,
  rows,
  hidden,
  summary,
  showAll,
  onToggleAll,
  isPending,
  error,
  onRetry,
  filtered,
  auditHitFetchCap,
}: {
  params: SourcesParams;
  rows: readonly Source[];
  hidden: number;
  summary: SourcesSummary;
  showAll: boolean;
  onToggleAll: () => void;
  isPending: boolean;
  error: Error | null;
  onRetry: () => void;
  filtered: boolean;
  auditHitFetchCap: boolean;
}) {
  if (isPending) return <SourcesTableSkeleton />;
  if (error) return <ErrorState title="Could not load the sources" message={error.message} onRetry={onRetry} />;
  if (summary.total === 0) {
    return filtered ? (
      <EmptyState variant="no-match" title="No sources match these filters" description="Try a different type, status or domain, or clear the filters." />
    ) : (
      <EmptyState
        title="No pages were researched"
        description="This audit has no source evidence. Page research may have been off, or no answer cited a page that could be fetched."
      />
    );
  }
  return (
    <>
      {summary.unreadable > 0 ? (
        <div className="mb-3">
          <Alert tone="warning" title={`${pluralize(summary.unreadable, "page")} could not be read`}>
            <p>
              Unreachable, blocked, paywalled, too large or errored pages say nothing about whether the brand is on
              them. They are not counted as &ldquo;brand not found&rdquo;.
            </p>
          </Alert>
        </div>
      ) : null}
      {auditHitFetchCap ? (
        <p className="mb-3 text-sm text-muted-foreground">
          This audit stopped fetching at its page limit, so this list is partial.
        </p>
      ) : null}
      <div role="region" aria-label="Sources table, scrolls sideways on small screens" tabIndex={0} className="overflow-x-auto rounded-lg border">
        <table className="w-full min-w-[52rem] text-sm">
          <caption className="sr-only">Researched pages</caption>
          <thead className="bg-muted text-left">
            <tr>
              <th scope="col" className="px-3 py-2 font-semibold">Domain</th>
              <th scope="col" className="px-3 py-2 font-semibold">Type</th>
              <th scope="col" className="px-3 py-2 font-semibold">Fetch status</th>
              <th scope="col" className="px-3 py-2 font-semibold">Brand on page</th>
              <th scope="col" className="px-3 py-2 font-semibold">Snapshot taken</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {rows.map((s) => (
              <tr key={s.id} data-testid="source-row">
                <th scope="row" className="px-3 py-2 text-left font-medium">
                  <Link
                    href={`/sources${serializeSourcesParams({ ...params, sourceId: s.id })}`}
                    className="break-all underline-offset-4 hover:underline"
                    aria-label={`Open ${s.domain}: ${s.url}`}
                  >
                    {s.domain}
                  </Link>
                  <span className="block max-w-xs truncate text-xs font-normal text-muted-foreground" title={s.url}>
                    {safeHref(s.url) ? s.url : `${s.url} (text only)`}
                  </span>
                </th>
                <td className="px-3 py-2">{SOURCE_TYPE_LABEL[s.source_type]}</td>
                <td className="px-3 py-2"><FetchStatusChip status={s.fetch_status} /></td>
                <td className="px-3 py-2"><EvidenceBadge evidence={s} /></td>
                <td className="px-3 py-2 whitespace-nowrap">
                  {s.snapshot_fetched_at ? <time dateTime={s.snapshot_fetched_at}>{formatDateTime(s.snapshot_fetched_at)}</time> : "No snapshot"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-sm text-muted-foreground" data-testid="source-count">
        {hidden > 0 ? `Showing the first ${rows.length} of ${summary.total} sources.` : `${pluralize(summary.total, "source")}${filtered ? " match" : ""}.`}{" "}
        {summary.found} with the brand, {summary.notFound} read without it, {summary.unreadable} could not be read.
        {hidden > 0 || showAll ? (
          <Button variant="link" size="sm" onClick={onToggleAll} className="ml-1 h-auto p-0">
            {showAll ? "Show fewer" : `Show all ${summary.total}`}
          </Button>
        ) : null}
      </p>
    </>
  );
}
