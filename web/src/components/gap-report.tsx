import Link from "next/link";
import { Target } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/view-states";
import type { components } from "@/lib/api/schema.gen";
import { pluralize } from "@/lib/format";
import { serializeSourcesParams, type SourcesParams } from "@/lib/source-filters";

type Entry = components["schemas"]["GapEntryOut"];

export function GapReportSkeleton() {
  return (
    <div role="status" aria-label="Loading the gap report" className="space-y-2">
      <span className="sr-only">Loading the gap report</span>
      {[0, 1, 2].map((i) => (
        <Skeleton key={i} className="h-10" />
      ))}
    </div>
  );
}

function evidenceHref(params: SourcesParams, sourceId: string): string {
  return `/sources${serializeSourcesParams({ ...params, sourceType: null, fetchStatus: null, domain: "", sourceId })}`;
}

export function GapReport({
  params,
  rows,
  total,
  hidden,
  showAll,
  onToggleAll,
  isPending,
  error,
  onRetry,
  auditRunning,
}: {
  params: SourcesParams;
  rows: readonly Entry[];
  total: number;
  hidden: number;
  showAll: boolean;
  onToggleAll: () => void;
  isPending: boolean;
  error: Error | null;
  onRetry: () => void;
  auditRunning: boolean;
}) {
  return (
    <section aria-labelledby="gap-heading" className="mb-8">
      <h2 id="gap-heading" className="flex items-center gap-2 text-lg font-semibold">
        <Target aria-hidden="true" className="size-5" />
        Source gap report
      </h2>
      <p className="mb-3 mt-1 text-sm text-muted-foreground">
        Websites that AI answers cited when talking about your competitors, but never cited for your brand. The most
        cited come first. Computed from the stored citations, not written by a model.
      </p>
      {isPending ? (
        <GapReportSkeleton />
      ) : error ? (
        <ErrorState title="Could not load the gap report" message={error.message} onRetry={onRetry} />
      ) : total === 0 ? (
        <EmptyState
          title="No source gaps found"
          description={
            auditRunning
              ? "No gaps yet. The audit is still running, so this can change as answers arrive."
              : "Every website cited for a competitor was also cited for your brand, or no competitor citations were recorded."
          }
        />
      ) : (
        <>
          <div role="region" aria-label="Gap report table, scrolls sideways on small screens" tabIndex={0} className="overflow-x-auto rounded-lg border">
            <table className="w-full min-w-[40rem] text-sm">
              <caption className="sr-only">Domains cited for competitors but never for the brand, most cited first</caption>
              <thead className="bg-muted text-left">
                <tr>
                  <th scope="col" className="px-3 py-2 font-semibold">Rank</th>
                  <th scope="col" className="px-3 py-2 font-semibold">Domain</th>
                  <th scope="col" className="px-3 py-2 text-right font-semibold">Times cited</th>
                  <th scope="col" className="px-3 py-2 font-semibold">Cited for competitors</th>
                  <th scope="col" className="px-3 py-2 font-semibold">Evidence</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {rows.map((e, i) => (
                  <tr key={e.domain}>
                    <td className="px-3 py-2 tabular-nums">{i + 1}</td>
                    <th scope="row" className="px-3 py-2 text-left font-medium break-all">{e.domain}</th>
                    <td className="px-3 py-2 text-right tabular-nums">{e.frequency}</td>
                    <td className="px-3 py-2 break-words">{e.competitors.length > 0 ? e.competitors.join(", ") : "Not recorded"}</td>
                    <td className="px-3 py-2">
                      {e.source_ids.length > 0 ? (
                        <span className="flex flex-col gap-0.5">
                          <Link href={evidenceHref(params, e.source_ids[0]!)} className="underline underline-offset-2">
                            Open the evidence<span className="sr-only"> for {e.domain}</span>
                          </Link>
                          {e.source_ids.length > 1 ? (
                            <span className="text-xs text-muted-foreground">
                              {pluralize(e.source_ids.length, "page")} checked from this domain
                            </span>
                          ) : null}
                        </span>
                      ) : (
                        <span className="text-muted-foreground">No page was fetched for this domain</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-sm text-muted-foreground" data-testid="gap-count">
            {hidden > 0 ? `Showing the top ${rows.length} of ${total} domains.` : `${pluralize(total, "domain")} in total.`}
            {total > rows.length || showAll ? (
              <Button variant="link" size="sm" onClick={onToggleAll} className="ml-1 h-auto p-0">
                {showAll ? "Show only the top domains" : `Show all ${total}`}
              </Button>
            ) : null}
          </p>
        </>
      )}
    </section>
  );
}
