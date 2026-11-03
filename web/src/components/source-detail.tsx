"use client";

import { useEffect, useRef, useState } from "react";
import { Clock, X } from "lucide-react";
import { EvidenceBadge, FetchStatusChip } from "@/components/evidence-badge";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/view-states";
import type { components } from "@/lib/api/schema.gen";
import { presentEvidence } from "@/lib/evidence";
import type { Freshness } from "@/lib/freshness";
import { formatDateTime } from "@/lib/format";
import { safeHref } from "@/lib/safe-url";
import { SOURCE_TYPE_LABEL } from "@/lib/source-filters";
import { snapshotView } from "@/lib/snapshot";

type Detail = components["schemas"]["SourceDetailOut"];

function SnapshotText({ text, quote }: { text: string; quote: string | null }) {
  const [expanded, setExpanded] = useState(false);
  const view = snapshotView(text, quote, expanded);
  return (
    <div>
      <div
        role="region"
        aria-label="Page snapshot text"
        tabIndex={0}
        className="max-h-[32rem] overflow-auto rounded-md border bg-muted/40 p-3 text-sm leading-relaxed whitespace-pre-wrap break-words"
      >
        {view.segments.map((s, i) =>
          s.highlighted ? (
            <mark
              key={i}
              data-testid="snapshot-highlight"
              className="rounded-sm bg-warning-bg px-0.5 text-foreground underline decoration-warning decoration-2 underline-offset-2 outline outline-1 outline-warning/70"
            >
              <span className="sr-only">[brand quote starts] </span>
              {s.text}
              <span className="sr-only"> [brand quote ends]</span>
            </mark>
          ) : (
            <span key={i}>{s.text}</span>
          ),
        )}
      </div>
      {quote && !view.located ? (
        <p role="note" className="mt-2 text-sm text-muted-foreground">
          The stored brand quote could not be placed in this snapshot text, so nothing is highlighted. Quote: &ldquo;{quote}&rdquo;
        </p>
      ) : null}
      {view.truncated ? (
        <p className="mt-2 text-sm text-muted-foreground" data-testid="snapshot-truncated">
          Showing the first {view.shown.toLocaleString("en-US")} of {view.total.toLocaleString("en-US")} characters.{" "}
          <Button variant="link" size="sm" className="h-auto p-0" onClick={() => setExpanded(true)}>
            Show the rest
          </Button>
        </p>
      ) : (
        <p className="mt-2 text-sm text-muted-foreground">{view.total.toLocaleString("en-US")} characters, all shown.</p>
      )}
    </div>
  );
}

export function SourceDetailSkeleton() {
  return (
    <div role="status" aria-label="Loading source" className="space-y-3 rounded-lg border p-4">
      <span className="sr-only">Loading source</span>
      <Skeleton className="h-6 w-64" />
      <Skeleton className="h-32" />
    </div>
  );
}

export function SourceDetail({
  data,
  freshness,
  isPending,
  notFound,
  error,
  onRetry,
  onClose,
}: {
  data: Detail | undefined;
  freshness: Freshness | null;
  isPending: boolean;
  notFound: boolean;
  error: Error | null;
  onRetry: () => void;
  onClose: () => void;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  const id = data?.id;
  useEffect(() => {
    heading.current?.focus(); // move focus to the opened detail
  }, [id, isPending]);

  const shell = (children: React.ReactNode) => (
    <section aria-labelledby="source-heading" className="mb-8 rounded-lg border p-4">
      <div className="mb-3 flex items-start justify-between gap-3">
        <h2 id="source-heading" ref={heading} tabIndex={-1} className="text-lg font-semibold break-all">
          {data ? data.domain : "Source"}
        </h2>
        <Button variant="outline" size="sm" onClick={onClose}>
          <X aria-hidden="true" />
          Close
        </Button>
      </div>
      {children}
    </section>
  );

  if (isPending) return shell(<SourceDetailSkeleton />);
  if (notFound) {
    return shell(
      <Alert tone="info" title="Source not found">
        <p>This audit has no source with that id. It may belong to another audit.</p>
      </Alert>,
    );
  }
  if (error || !data) return shell(<ErrorState title="Could not load this source" message={error?.message ?? "Try again."} onRetry={onRetry} />);

  const p = presentEvidence(data);
  const href = safeHref(data.url);
  const redirected = data.final_url !== null && data.final_url !== data.url;

  return shell(
    <div className="space-y-4">
      <div className="space-y-1 text-sm">
        <p className="break-all">
          {href ? (
            <a href={href} target="_blank" rel="noopener noreferrer nofollow" className="underline underline-offset-2">
              {data.url}
              <span className="sr-only"> (opens in a new tab)</span>
            </a>
          ) : (
            `${data.url} (not a web address, shown as text only)`
          )}
        </p>
        {redirected ? (
          <p className="break-all text-muted-foreground">
            Redirected to {data.final_url}. The evidence is attributed to the final domain.
          </p>
        ) : null}
        <div className="flex flex-wrap items-center gap-1.5 pt-1">
          <span className="rounded-md border px-2 py-0.5 text-xs font-medium">{SOURCE_TYPE_LABEL[data.source_type]}</span>
          <FetchStatusChip status={data.fetch_status} />
          <EvidenceBadge evidence={data} />
        </div>
        {p.reason ? <p className="text-muted-foreground">{p.reason}</p> : null}
      </div>

      <div
        className={
          freshness?.stale
            ? "flex items-start gap-2 rounded-md border border-warning/60 bg-warning-bg p-3 text-sm"
            : "flex items-start gap-2 rounded-md border p-3 text-sm"
        }
        data-testid="snapshot-time"
      >
        <Clock aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
        <p>
          {data.snapshot_fetched_at ? (
            <>
              Snapshot taken <time dateTime={data.snapshot_fetched_at}>{formatDateTime(data.snapshot_fetched_at)}</time>
              {freshness ? <> ({freshness.ago})</> : null}.
              {freshness?.stale ? " This snapshot is a week or more old; the live page may have changed since." : " This is what the page said at that time, not necessarily what it says now."}
            </>
          ) : (
            "No snapshot time was recorded for this source."
          )}
          {data.content_hash ? <span className="block text-xs text-muted-foreground">Content hash {data.content_hash.slice(0, 12)}</span> : null}
        </p>
      </div>

      {p.kind === "unreadable" ? (
        <Alert tone="warning" title="This page could not be read, so there is no snapshot to show">
          <p>{p.meaning}</p>
        </Alert>
      ) : data.snapshot_text === null ? (
        <Alert tone="info" title="No snapshot text is stored for this source">
          <p>The page was marked as read, but no text was kept, so there is nothing to display.</p>
        </Alert>
      ) : (
        <div>
          <h3 className="mb-2 text-base font-semibold">Page snapshot</h3>
          <p className="mb-2 text-sm text-muted-foreground">
            {p.kind === "found"
              ? "The highlighted text is the brand quote found on the page."
              : "The page was read and the brand does not appear in it, so nothing is highlighted."}{" "}
            The text is shown as plain text exactly as stored.
          </p>
          <SnapshotText key={data.id} text={data.snapshot_text} quote={data.quote} />
        </div>
      )}
    </div>,
  );
}
