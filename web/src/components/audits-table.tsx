import Link from "next/link";
import { Flag } from "lucide-react";
import { CapsChips, DegradedChip, StatusChip } from "@/components/status-chip";
import type { components } from "@/lib/api/schema.gen";
import { formatDateTime, formatRate, formatUsd } from "@/lib/format";

type Item = components["schemas"]["AuditListItem"];

export function AuditsTable({ items }: { items: readonly Item[] }) {
  return (
    <div
      role="region"
      aria-label="Audits table, scrolls sideways on small screens"
      tabIndex={0}
      className="overflow-x-auto rounded-lg border focus-visible:outline-2 focus-visible:outline-ring"
    >
      <table className="w-full min-w-[44rem] text-sm">
        <caption className="sr-only">Audits, newest first</caption>
        <thead className="bg-muted text-left">
          <tr>
            <th scope="col" className="px-3 py-2 font-semibold">Brand</th>
            <th scope="col" className="px-3 py-2 font-semibold">Status</th>
            <th scope="col" className="px-3 py-2 text-right font-semibold">Mention rate</th>
            <th scope="col" className="px-3 py-2 text-right font-semibold">Flagged</th>
            <th scope="col" className="px-3 py-2 text-right font-semibold">Cost</th>
            <th scope="col" className="px-3 py-2 font-semibold">Created</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {items.map((audit) => (
            <tr key={audit.id}>
              <th scope="row" className="px-3 py-2 text-left font-medium">
                <Link href={`/audits/${audit.id}`} className="underline-offset-4 hover:underline">
                  {audit.brand}
                </Link>
              </th>
              <td className="px-3 py-2">
                <div className="flex flex-wrap items-center gap-1">
                  <StatusChip status={audit.status} />
                  {audit.degraded ? <DegradedChip /> : null}
                  <CapsChips caps={audit.caps_hit} />
                </div>
              </td>
              <td className="px-3 py-2 text-right tabular-nums">
                {audit.mention_rate === null ? (
                  <span title="No usable answers yet">
                    <span aria-hidden="true">{formatRate(null)}</span>
                    <span className="sr-only">Not available</span>
                  </span>
                ) : (
                  formatRate(audit.mention_rate)
                )}
              </td>
              <td className="px-3 py-2 text-right tabular-nums">
                {audit.flagged_count > 0 ? (
                  <span className="inline-flex items-center gap-1 font-medium">
                    <Flag aria-hidden="true" className="size-3.5" />
                    {audit.flagged_count}
                    <span className="sr-only"> flagged, waiting for review</span>
                  </span>
                ) : (
                  0
                )}
              </td>
              <td className="px-3 py-2 text-right tabular-nums">{formatUsd(audit.total_cost_usd)}</td>
              <td className="px-3 py-2">
                <time dateTime={audit.created_at}>{formatDateTime(audit.created_at)}</time>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function AuditsTableSkeleton() {
  return (
    <div role="status" aria-label="Loading audits" className="space-y-2">
      <span className="sr-only">Loading audits</span>
      {Array.from({ length: 5 }, (_, i) => (
        <div key={i} aria-hidden="true" className="h-12 animate-pulse rounded-md bg-muted" />
      ))}
    </div>
  );
}
