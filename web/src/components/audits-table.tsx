"use client";

import Link from "next/link";
import { CapsChips, DegradedChip, StatusChip } from "@/components/status-chip";
import { DeleteAuditButton } from "@/components/delete-audit-button";
import type { components } from "@/lib/api/schema.gen";
import { formatDateTime, formatRate } from "@/lib/format";

type Item = components["schemas"]["AuditListItem"];

export function AuditsTable({ items }: { items: readonly Item[] }) {
  return (
    <div
      role="region"
      aria-label="Audits table, scrolls sideways on small screens"
      tabIndex={0}
      className="overflow-x-auto rounded-lg border focus-visible:outline-2 focus-visible:outline-ring"
    >
      <table className="w-full min-w-[40rem] text-sm">
        <caption className="sr-only">Audits, newest first</caption>
        <thead className="bg-muted text-left">
          <tr>
            <th scope="col" className="px-3 py-2 font-semibold">Brand</th>
            <th scope="col" className="px-3 py-2 font-semibold">Status</th>
            <th scope="col" className="px-3 py-2 text-right font-semibold">AI visibility</th>
            <th scope="col" className="px-3 py-2 font-semibold">Created</th>
            <th scope="col" className="px-3 py-2 text-right font-semibold">Actions</th>
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
              <td className="px-3 py-2">
                <time dateTime={audit.created_at}>{formatDateTime(audit.created_at)}</time>
              </td>
              <td className="px-3 py-2 text-right">
                <div className="flex justify-end">
                  <DeleteAuditButton
                    auditId={audit.id}
                    brand={audit.brand}
                    disabled={audit.status === "running"}
                    onDeleted={() => undefined}
                  />
                </div>
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
