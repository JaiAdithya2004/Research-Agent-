"use client";

import Link from "next/link";
import { Plus } from "lucide-react";
import { AuditsTable, AuditsTableSkeleton } from "@/components/audits-table";
import { PageHeader } from "@/components/page-header";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { EmptyState, ErrorState } from "@/components/view-states";
import { useAuditFilters } from "@/hooks/use-audit-filters";
import { useAuditsList } from "@/hooks/use-audits-list";
import { STATUS_VALUES, hasFilters, MAX_BRAND_FILTER, type AuditStatus } from "@/lib/audit-filters";
import { presentStatus } from "@/lib/audit-status";
import { cn } from "@/lib/utils";

export function AuditsView() {
  const { filters, brandInput, setBrandInput, setStatus, clear } = useAuditFilters();
  const list = useAuditsList(filters);
  // A failed "load more" also sets isError, but the rows already loaded must stay on screen.
  const initialError = list.isError && !list.nextPageError;
  const filtered = hasFilters(filters) || brandInput.trim() !== "";

  return (
    <>
      <PageHeader
        title="Audits"
        description="Every audit, newest first."
        actions={
          <Link href="/audits/new" className={cn(buttonVariants())}>
            <Plus aria-hidden="true" />
            New audit
          </Link>
        }
      />

      <form
        role="search"
        aria-label="Filter audits"
        className="mb-4 flex flex-wrap items-end gap-3"
        onSubmit={(e) => e.preventDefault()}
      >
        <div className="flex min-w-48 flex-1 flex-col gap-1">
          <label htmlFor="filter-brand" className="text-sm font-medium">
            Brand
          </label>
          <Input
            id="filter-brand"
            type="search"
            value={brandInput}
            maxLength={MAX_BRAND_FILTER}
            placeholder="Search by brand"
            onChange={(e) => setBrandInput(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor="filter-status" className="text-sm font-medium">
            Status
          </label>
          <Select
            id="filter-status"
            value={filters.status ?? ""}
            onChange={(e) => setStatus((e.target.value || null) as AuditStatus | null)}
          >
            <option value="">All statuses</option>
            {STATUS_VALUES.map((s) => (
              <option key={s} value={s}>
                {presentStatus(s).label}
              </option>
            ))}
          </Select>
        </div>
        {filtered ? (
          <Button variant="ghost" onClick={clear}>
            Clear filters
          </Button>
        ) : null}
      </form>

      <div aria-live="polite" className="sr-only" data-testid="list-live-region">
        {list.isSuccess ? `${list.items.length} audits shown${list.hasNextPage ? ", more available" : ""}` : ""}
      </div>

      {list.isPending ? (
        <AuditsTableSkeleton />
      ) : initialError ? (
        <ErrorState
          title="Could not load audits"
          message={list.error?.message ?? "Try again."}
          onRetry={() => void list.refetch()}
          retrying={list.isRefetching}
        />
      ) : list.items.length === 0 ? (
        filtered ? (
          <EmptyState
            variant="no-match"
            title="No audits match these filters"
            description="Try a different status or brand, or clear the filters to see every audit."
            action={<Button onClick={clear}>Clear filters</Button>}
          />
        ) : (
          <EmptyState
            title="No audits yet"
            description="An audit asks AI assistants your buyer questions and checks whether your brand appears. Start your first one."
            action={
              <Link href="/audits/new" className={cn(buttonVariants())}>
                <Plus aria-hidden="true" />
                Create your first audit
              </Link>
            }
          />
        )
      ) : (
        <>
          <AuditsTable items={list.items} />
          {list.nextPageError ? (
            <div className="mt-4">
              <ErrorState
                title="Could not load more audits"
                message={list.nextPageError.message}
                onRetry={() => void list.fetchNextPage()}
                retrying={list.isFetchingNextPage}
              />
            </div>
          ) : null}
          {list.hasNextPage ? (
            list.nextPageError ? null : (
              <div className="mt-4 flex justify-center">
                <Button variant="outline" onClick={() => void list.fetchNextPage()} disabled={list.isFetchingNextPage}>
                  {list.isFetchingNextPage ? "Loading…" : "Load more audits"}
                </Button>
              </div>
            )
          ) : (
            <p className="mt-4 text-center text-sm text-muted-foreground">That is every audit.</p>
          )}
        </>
      )}
    </>
  );
}
