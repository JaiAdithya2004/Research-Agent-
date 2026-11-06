import type { Segment } from "@/lib/highlight";

/**
 * A small, display-only reader for the Markdown-ish text that models write: headings, rules,
 * bullets, numbered items, paragraphs and **bold**. It never produces HTML: it returns plain text
 * pieces that React renders as text nodes. The raw answer is not changed; the exact text stays
 * available in the "Exact text" view. Highlight state is carried through so the extracted quote
 * stays marked in the formatted view.
 */

export interface Piece {
  text: string;
  highlighted: boolean;
  bold: boolean;
}

export type Block =
  | { kind: "heading"; level: number; pieces: Piece[] }
  | { kind: "paragraph"; pieces: Piece[] }
  | { kind: "bullet"; indent: number; pieces: Piece[] }
  | { kind: "numbered"; number: string; indent: number; pieces: Piece[] }
  | { kind: "rule"; pieces: Piece[] };

interface Raw {
  text: string;
  highlighted: boolean;
}

/** Cut the answer into lines, keeping which characters are inside the highlighted quote. */
function toLines(segments: readonly Segment[]): Raw[][] {
  const lines: Raw[][] = [[]];
  for (const seg of segments) {
    const parts = seg.text.split("\n");
    parts.forEach((part, i) => {
      if (i > 0) lines.push([]);
      if (part !== "") lines[lines.length - 1]!.push({ text: part, highlighted: seg.highlighted });
    });
  }
  return lines;
}

function dropPrefix(pieces: readonly Raw[], count: number): Raw[] {
  let left = count;
  const out: Raw[] = [];
  for (const p of pieces) {
    if (left >= p.text.length) {
      left -= p.text.length;
      continue;
    }
    out.push({ text: p.text.slice(left), highlighted: p.highlighted });
    left = 0;
  }
  return out;
}

function tidy(text: string): string {
  return text.replace(/\$\rightarrow\$/g, "→");
}

function inline(pieces: readonly Raw[]): Piece[] {
  const out: Piece[] = [];
  for (const p of pieces) {
    const text = tidy(p.text);
    text.split(/(\*\*[^*\n]+\*\*)/).forEach((chunk) => {
      if (chunk === "") return;
      const bold = chunk.length > 4 && chunk.startsWith("**") && chunk.endsWith("**");
      out.push({ text: bold ? chunk.slice(2, -2) : chunk, highlighted: p.highlighted, bold });
    });
  }
  return out;
}

export function parseAnswer(segments: readonly Segment[]): Block[] {
  const blocks: Block[] = [];
  for (const line of toLines(segments)) {
    const full = line.map((p) => p.text).join("");
    if (full.trim() === "") continue;
    const heading = /^(#{1,6})\s+/.exec(full);
    const bullet = /^(\s*)[*-]\s+/.exec(full);
    const numbered = /^(\s*)(\d+)[.)]\s+/.exec(full);
    if (heading) {
      blocks.push({ kind: "heading", level: heading[1]!.length, pieces: inline(dropPrefix(line, heading[0].length)) });
    } else if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(full)) {
      blocks.push({ kind: "rule", pieces: [] });
    } else if (bullet) {
      const indent = Math.min(2, Math.floor(bullet[1]!.length / 2));
      blocks.push({ kind: "bullet", indent, pieces: inline(dropPrefix(line, bullet[0].length)) });
    } else if (numbered) {
      const indent = Math.min(2, Math.floor(numbered[1]!.length / 2));
      blocks.push({
        kind: "numbered",
        number: numbered[2]!,
        indent,
        pieces: inline(dropPrefix(line, numbered[0].length)),
      });
    } else {
      blocks.push({ kind: "paragraph", pieces: inline(line) });
    }
  }
  return blocks;
}
