"use client";

import { useState, type ReactNode } from "react";
import { buildSegments, type Span } from "@/lib/highlight";
import { parseAnswer, type Block, type Piece } from "@/lib/markdown";
import { cn } from "@/lib/utils";

const MARK_CLASS =
  "rounded-sm bg-warning-bg px-0.5 text-foreground underline decoration-warning decoration-2 underline-offset-2 outline outline-1 outline-warning/70";

function Marked({ children }: { children: ReactNode }) {
  return (
    <mark data-testid="quote-highlight" className={MARK_CLASS}>
      <span className="sr-only">[extracted quote starts] </span>
      {children}
      <span className="sr-only"> [extracted quote ends]</span>
    </mark>
  );
}

/** Adjacent highlighted pieces share one <mark>, so the quote reads as one unit. */
function Pieces({ pieces }: { pieces: readonly Piece[] }) {
  const groups: { highlighted: boolean; items: Piece[] }[] = [];
  for (const p of pieces) {
    const last = groups[groups.length - 1];
    if (last && last.highlighted === p.highlighted) last.items.push(p);
    else groups.push({ highlighted: p.highlighted, items: [p] });
  }
  return (
    <>
      {groups.map((g, gi) => {
        const content = g.items.map((p, i) => (p.bold ? <strong key={i}>{p.text}</strong> : <span key={i}>{p.text}</span>));
        return g.highlighted ? <Marked key={gi}>{content}</Marked> : <span key={gi}>{content}</span>;
      })}
    </>
  );
}

const HEADING_CLASS: Record<number, string> = {
  1: "mt-5 text-xl font-semibold",
  2: "mt-5 text-lg font-semibold",
  3: "mt-4 text-base font-semibold",
};
const INDENT = ["", "ml-5", "ml-10"];

function BlockView({ block }: { block: Block }) {
  switch (block.kind) {
    case "heading":
      return (
        <p role="heading" aria-level={Math.min(6, block.level + 2)} className={HEADING_CLASS[block.level] ?? "mt-3 font-semibold"}>
          <Pieces pieces={block.pieces} />
        </p>
      );
    case "rule":
      return <hr className="my-3 border-border" />;
    case "bullet":
      return (
        <p className={cn("flex gap-2", INDENT[block.indent])}>
          <span aria-hidden="true">•</span>
          <span className="min-w-0">
            <Pieces pieces={block.pieces} />
          </span>
        </p>
      );
    case "numbered":
      return (
        <p className={cn("flex gap-2", INDENT[block.indent])}>
          <span className="tabular-nums">{block.number}.</span>
          <span className="min-w-0">
            <Pieces pieces={block.pieces} />
          </span>
        </p>
      );
    case "paragraph":
      return (
        <p>
          <Pieces pieces={block.pieces} />
        </p>
      );
  }
}

/**
 * The answer with the extracted quote marked in place. The text is untrusted model output: it is
 * rendered only as React text nodes (never as HTML). "Formatted" is an easier read; "Exact text"
 * is the raw answer character for character. Both mark the quote with an underline and
 * screen-reader boundaries so colour is not the only cue.
 */
export function AnswerText({
  text,
  spans,
  quote,
  label = "Answer text",
}: {
  text: string | null;
  spans: readonly Span[];
  quote: string | null;
  label?: string;
}) {
  const [view, setView] = useState<"formatted" | "exact">("formatted");
  if (text === null || text === "") {
    return (
      <p className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">
        The raw answer is not available for this run.
      </p>
    );
  }
  const { segments, ignoredSpans } = buildSegments(text, spans);
  const drawn = segments.some((s) => s.highlighted);
  const tab = (value: "formatted" | "exact", name: string) => (
    <button
      type="button"
      aria-pressed={view === value}
      onClick={() => setView(value)}
      className={cn(
        "rounded-md border px-2.5 py-1 text-sm font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2",
        view === value ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:bg-accent",
      )}
    >
      {name}
    </button>
  );
  return (
    <div>
      <div role="group" aria-label="Answer view" className="mb-2 flex gap-2">
        {tab("formatted", "Formatted")}
        {tab("exact", "Exact text")}
      </div>
      <div
        role="region"
        aria-label={label}
        tabIndex={0}
        className={cn(
          "max-h-[32rem] overflow-auto rounded-md border bg-muted/40 p-4 text-sm leading-relaxed break-words",
          view === "exact" ? "whitespace-pre-wrap" : "space-y-2",
        )}
      >
        {view === "exact"
          ? segments.map((s, i) =>
              s.highlighted ? <Marked key={i}>{s.text}</Marked> : <span key={i}>{s.text}</span>,
            )
          : parseAnswer(segments).map((b, i) => <BlockView key={i} block={b} />)}
      </div>
      {!drawn && quote ? (
        <p role="note" className="mt-2 text-sm text-muted-foreground">
          The extractor reported a quote, but it could not be placed in the raw answer, so nothing is highlighted
          {ignoredSpans > 0 ? " (the position data did not fit the text)" : ""}. Quote reported: &ldquo;{quote}&rdquo;
        </p>
      ) : null}
      {!drawn && !quote ? <p className="mt-2 text-sm text-muted-foreground">No quote was extracted from this answer.</p> : null}
    </div>
  );
}
