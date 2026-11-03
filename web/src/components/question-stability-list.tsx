import Link from "next/link";
import { GitCompareArrows } from "lucide-react";
import { agreementText, type Dot, type DotKind, type QuestionRow } from "@/lib/stability";

/** Four shapes, so no mark depends on colour: filled, hollow, crossed square, dashed. */
function DotGlyph({ kind }: { kind: DotKind }) {
  const common = { width: 18, height: 18, viewBox: "0 0 18 18", "aria-hidden": true, focusable: false } as const;
  switch (kind) {
    case "mentioned":
      return (
        <svg {...common} className="text-success">
          <circle cx="9" cy="9" r="7" fill="currentColor" />
        </svg>
      );
    case "absent":
      return (
        <svg {...common} className="text-foreground">
          <circle cx="9" cy="9" r="6.25" fill="none" stroke="currentColor" strokeWidth="2" />
        </svg>
      );
    case "failed":
      return (
        <svg {...common} className="text-destructive">
          <rect x="2" y="2" width="14" height="14" rx="2" fill="none" stroke="currentColor" strokeWidth="2" />
          <path d="M6 6l6 6M12 6l-6 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        </svg>
      );
    case "pending":
      return (
        <svg {...common} className="text-muted-foreground">
          <circle cx="9" cy="9" r="6.25" fill="none" stroke="currentColor" strokeWidth="2" strokeDasharray="3 3" />
        </svg>
      );
  }
}

function DotMark({ dot, auditId }: { dot: Dot; auditId: string }) {
  if (dot.runId) {
    return (
      <Link
        href={`/audits/${auditId}/runs/${dot.runId}`}
        aria-label={dot.label}
        title={dot.label}
        className="inline-flex size-8 items-center justify-center rounded-md hover:bg-accent"
      >
        <DotGlyph kind={dot.kind} />
      </Link>
    );
  }
  return (
    <span role="img" aria-label={dot.label} title={dot.label} className="inline-flex size-8 items-center justify-center">
      <DotGlyph kind={dot.kind} />
    </span>
  );
}

export function DotLegend() {
  const items: { kind: DotKind; text: string }[] = [
    { kind: "mentioned", text: "Brand mentioned" },
    { kind: "absent", text: "Not mentioned" },
    { kind: "failed", text: "Run failed, no usable answer" },
    { kind: "pending", text: "Not run" },
  ];
  return (
    <ul aria-label="Legend" className="mb-3 flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground">
      {items.map((i) => (
        <li key={i.kind} className="inline-flex items-center gap-1.5">
          <DotGlyph kind={i.kind} />
          {i.text}
        </li>
      ))}
    </ul>
  );
}

export function QuestionStabilityList({ rows, auditId }: { rows: readonly QuestionRow[]; auditId: string }) {
  return (
    <ol className="flex flex-col gap-3" aria-label="Questions">
      {rows.map((row) => (
        <li key={row.index} className="rounded-lg border bg-card p-4" data-testid={`question-row-${row.index}`}>
          <div className="flex flex-wrap items-start justify-between gap-2">
            <h3 className="min-w-0 flex-1 text-base font-medium">
              <span className="text-muted-foreground">{row.index + 1}. </span>
              {row.question}
            </h3>
            {row.disagree ? (
              <span className="inline-flex items-center gap-1 rounded-full border border-warning/60 bg-warning-bg px-2 py-0.5 text-xs font-semibold text-warning">
                <GitCompareArrows aria-hidden="true" className="size-3.5" />
                Repeats disagree
              </span>
            ) : null}
          </div>
          {row.groups.length === 0 ? (
            <p className="mt-2 text-sm text-muted-foreground">No answers for this question yet.</p>
          ) : (
            <ul className="mt-2 flex flex-col gap-2">
              {row.groups.map((g) => (
                <li key={g.key} className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <span className="w-44 max-w-full text-sm text-muted-foreground" title={g.provider}>
                    {g.model}, {g.modeLabel}
                  </span>
                  <span className="flex items-center" role="group" aria-label={`Repeats for ${g.model}, ${g.modeLabel}`}>
                    {g.dots.map((d) => (
                      <DotMark key={d.repeat} dot={d} auditId={auditId} />
                    ))}
                  </span>
                  <span className="text-sm tabular-nums">
                    {g.usable > 0 ? `${g.mentioned} of ${g.usable} mention the brand` : "no usable repeats"}
                    <span className="text-muted-foreground">, {agreementText(g)}</span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </li>
      ))}
    </ol>
  );
}
