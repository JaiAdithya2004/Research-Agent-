import Link from "next/link";
import { BookOpenCheck, Info } from "lucide-react";
import { EvidenceBadge } from "@/components/evidence-badge";
import { Alert } from "@/components/ui/alert";
import type { components } from "@/lib/api/schema.gen";
import { summarizeCitations } from "@/lib/citations";
import { citationOrigin, presentEvidence } from "@/lib/evidence";
import { safeHref } from "@/lib/safe-url";
import { pluralize } from "@/lib/format";
import type { RunSearchMode } from "@/lib/stability";

type Citation = components["schemas"]["CitationOut"];

const SOURCE_TYPE: Record<string, string> = {
  own_site: "Own site",
  competitor: "Competitor",
  review: "Review site",
  news: "News",
  forum: "Forum",
  other: "Other",
};

export function CitationsPanel({
  citations,
  auditId,
  searchMode,
  researchCapped,
}: {
  citations: readonly Citation[];
  auditId: string;
  searchMode: RunSearchMode;
  /** The audit hit its page fetch cap, which is why some pages were never checked. */
  researchCapped: boolean;
}) {
  const s = summarizeCitations(citations);
  return (
    <section aria-labelledby="citations-heading" className="rounded-lg border p-4">
      <h2 id="citations-heading" className="flex items-center gap-2 text-lg font-semibold">
        <BookOpenCheck aria-hidden="true" className="size-5" />
        Sources the AI used
      </h2>

      {s.total === 0 ? (
        <p className="mt-3 text-sm text-muted-foreground">
          {searchMode === "off"
            ? "Search was off for this run, so the model was not asked for sources and none are expected."
            : "This answer cited no sources."}
        </p>
      ) : (
        <>
          <p className="mt-1 text-sm text-muted-foreground" data-testid="citation-summary">
            {pluralize(s.total, "source")}
            {s.proseOnly > 0 ? `, ${s.proseOnly} named only in the answer text and not verified` : ""}.{" "}
            Pages: {s.found} with the brand, {s.notFound} read without the brand, {s.unreadable} could not be read
            {s.notResearched > 0 ? `, ${s.notResearched} not checked` : ""}.
          </p>
          {s.unreadable > 0 ? (
            <div className="mt-3">
              <Alert tone="warning" title="Some pages could not be read">
                <p>
                  For {pluralize(s.unreadable, "page")} we do not know whether the brand is mentioned, because the page
                  was unreachable, blocked, paywalled or otherwise unreadable. These are not counted as &ldquo;brand
                  not found&rdquo;.
                </p>
              </Alert>
            </div>
          ) : null}
          {researchCapped && s.notResearched > 0 ? (
            <div className="mt-3">
              <Alert tone="info" title="The page fetch cap was reached">
                <p>Some pages below were not checked because the audit hit its limit on fetched pages.</p>
              </Alert>
            </div>
          ) : null}
          <ul className="mt-3 divide-y">
            {citations.map((c, i) => {
              const origin = citationOrigin(c);
              const evidence = presentEvidence(c.evidence);
              const href = safeHref(c.url);
              return (
                <li key={`${c.url}-${i}`} className="py-3" data-testid="citation">
                  <div className="flex flex-wrap items-baseline gap-x-2">
                    <span className="font-medium break-all">{c.domain}</span>
                    {c.title ? <span className="text-sm text-muted-foreground break-words">{c.title}</span> : null}
                  </div>
                  <p className="mt-0.5 text-xs break-all text-muted-foreground">
                    {href ? (
                      <a href={href} target="_blank" rel="noopener noreferrer nofollow" className="underline underline-offset-2">
                        {c.url}
                        <span className="sr-only"> (opens in a new tab)</span>
                      </a>
                    ) : (
                      <span>{c.url} (not a web address, shown as text only)</span>
                    )}
                  </p>
                  <div className="mt-2 flex flex-wrap items-center gap-1.5">
                    {origin.kind === "grounding" ? null : (
                      <span
                        title={origin.meaning}
                        className="inline-flex items-center gap-1 rounded-md border border-dashed border-warning/70 bg-warning-bg px-2 py-0.5 text-xs font-medium text-warning"
                      >
                        <Info aria-hidden="true" className="size-3.5" />
                        {origin.label}
                      </span>
                    )}
                    <EvidenceBadge evidence={c.evidence} />
                    {c.evidence ? (
                      <span className="text-xs text-muted-foreground">{SOURCE_TYPE[c.evidence.source_type] ?? c.evidence.source_type}</span>
                    ) : null}
                  </div>
                  {evidence.reason ? <p className="mt-1 text-xs text-muted-foreground">{evidence.reason}</p> : null}
                  {c.evidence ? (
                    <p className="mt-1 text-xs">
                      <Link
                        href={`/sources?audit_id=${encodeURIComponent(auditId)}&source=${encodeURIComponent(c.evidence.source_id)}`}
                        className="underline underline-offset-2"
                      >
                        Open the page snapshot
                      </Link>
                    </p>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}
