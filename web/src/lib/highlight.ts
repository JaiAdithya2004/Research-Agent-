/**
 * Turning the API's highlight spans into text segments.
 *
 * The backend finds the quote in the raw answer with Python, so offsets count Unicode code points
 * (Python `str` indexes), not UTF-16 units (JavaScript `string` indexes). Slicing by code point
 * keeps emoji and other astral characters from shifting the highlight. The text itself is never
 * altered or parsed: the components render each segment as a React text node.
 */

export interface Span {
  start: number;
  end: number;
}

export interface Segment {
  text: string;
  highlighted: boolean;
}

export interface SegmentedText {
  segments: Segment[];
  /** Spans that were outside the text, empty, or overlapped an earlier span, and so were not drawn. */
  ignoredSpans: number;
}

export function buildSegments(text: string, spans: readonly Span[]): SegmentedText {
  const points = Array.from(text);
  const usable: Span[] = [];
  let ignored = 0;
  const sorted = [...spans].sort((a, b) => a.start - b.start || a.end - b.end);
  let cursor = 0;
  for (const span of sorted) {
    const valid =
      Number.isInteger(span.start) &&
      Number.isInteger(span.end) &&
      span.start >= cursor &&
      span.end > span.start &&
      span.end <= points.length;
    if (!valid) {
      ignored += 1;
      continue;
    }
    usable.push(span);
    cursor = span.end;
  }
  const segments: Segment[] = [];
  let at = 0;
  for (const span of usable) {
    if (span.start > at) segments.push({ text: points.slice(at, span.start).join(""), highlighted: false });
    segments.push({ text: points.slice(span.start, span.end).join(""), highlighted: true });
    at = span.end;
  }
  if (at < points.length || segments.length === 0) segments.push({ text: points.slice(at).join(""), highlighted: false });
  return { segments, ignoredSpans: ignored };
}
