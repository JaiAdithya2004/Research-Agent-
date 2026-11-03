import { buildSegments, type Span } from "@/lib/highlight";

/**
 * The raw answer with the extracted quote marked in place. The text is untrusted model output: it
 * is rendered only as React text nodes (never as HTML), inside a <mark> where the API's offsets say
 * the quote is. The mark has an underline and screen-reader boundaries so colour is not the only cue.
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
  if (text === null || text === "") {
    return (
      <p className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">
        The raw answer is not available for this run.
      </p>
    );
  }
  const { segments, ignoredSpans } = buildSegments(text, spans);
  const drawn = segments.some((s) => s.highlighted);
  return (
    <div>
      <div
        role="region"
        aria-label={label}
        tabIndex={0}
        className="max-h-[32rem] overflow-auto rounded-md border bg-muted/40 p-3 text-sm leading-relaxed whitespace-pre-wrap break-words"
      >
        {segments.map((s, i) =>
          s.highlighted ? (
            <mark
              key={i}
              data-testid="quote-highlight"
              className="rounded-sm bg-warning-bg px-0.5 text-foreground underline decoration-warning decoration-2 underline-offset-2 outline outline-1 outline-warning/70"
            >
              <span className="sr-only">[extracted quote starts] </span>
              {s.text}
              <span className="sr-only"> [extracted quote ends]</span>
            </mark>
          ) : (
            <span key={i}>{s.text}</span>
          ),
        )}
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
