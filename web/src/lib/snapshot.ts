import type { Segment } from "@/lib/highlight";

/** Snapshot text is shown in full up to this many characters, then behind an explicit "show the rest". */
export const SNAPSHOT_DISPLAY_CAP = 50_000;

const WORD = "\\p{L}\\p{N}_";
const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

export interface Utf16Span {
  start: number;
  end: number;
}

/**
 * Where the brand quote sits in the snapshot text (UTF-16 indexes, for slicing JS strings).
 * Mirrors the backend's leniency: an exact whole-word match first, then one that ignores case,
 * spacing and punctuation. The quote was verified against this same text, so a miss means the
 * stored quote and the text differ in some way we do not guess at; the caller says so.
 */
export function locateQuote(text: string, quote: string | null): Utf16Span | null {
  const stripped = quote?.trim() ?? "";
  if (!text || !stripped) return null;
  const bounded = (body: string, first: string, last: string) =>
    `${new RegExp(`[${WORD}]`, "u").test(first) ? `(?<![${WORD}])` : ""}${body}${new RegExp(`[${WORD}]`, "u").test(last) ? `(?![${WORD}])` : ""}`;
  const exact = new RegExp(bounded(escape(stripped), stripped[0]!, stripped[stripped.length - 1]!), "u").exec(text);
  if (exact) return { start: exact.index, end: exact.index + exact[0].length };
  const tokens = stripped.toLowerCase().split(/[^\p{L}\p{N}]+/u).filter(Boolean);
  if (tokens.length === 0) return null;
  const loose = new RegExp(
    bounded(tokens.map(escape).join(`[^\\p{L}\\p{N}]+`), tokens[0]![0]!, tokens[tokens.length - 1]!.slice(-1)),
    "iu",
  ).exec(text);
  return loose ? { start: loose.index, end: loose.index + loose[0].length } : null;
}

export interface SnapshotView {
  segments: Segment[];
  total: number;
  shown: number;
  /** Part of the text is not on screen yet. */
  truncated: boolean;
  /** The quote was found in the text and is highlighted. */
  located: boolean;
}

function safeCut(text: string, at: number): number {
  const code = text.charCodeAt(at - 1);
  return code >= 0xd800 && code <= 0xdbff ? at - 1 : at; // never split a surrogate pair
}

export function snapshotView(text: string, quote: string | null, expanded: boolean, cap = SNAPSHOT_DISPLAY_CAP): SnapshotView {
  const span = locateQuote(text, quote);
  // A highlight past the cap would be invisible, so in that case show everything.
  const showAll = expanded || text.length <= cap || (span !== null && span.end > cap);
  const limit = showAll ? text.length : safeCut(text, cap);
  const shownText = text.slice(0, limit);
  const segments: Segment[] = [];
  if (span && span.end <= limit) {
    if (span.start > 0) segments.push({ text: shownText.slice(0, span.start), highlighted: false });
    segments.push({ text: shownText.slice(span.start, span.end), highlighted: true });
    if (span.end < limit) segments.push({ text: shownText.slice(span.end), highlighted: false });
  } else {
    segments.push({ text: shownText, highlighted: false });
  }
  return { segments, total: text.length, shown: limit, truncated: limit < text.length, located: span !== null };
}
