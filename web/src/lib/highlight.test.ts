import { describe, expect, it } from "vitest";
import { buildSegments } from "./highlight";

describe("buildSegments", () => {
  it("splits the raw text at exact offsets and keeps the text unchanged", () => {
    const text = "Top tools: Acme CRM and Rival.";
    const r = buildSegments(text, [{ start: 11, end: 19 }]);
    expect(r.segments).toEqual([
      { text: "Top tools: ", highlighted: false },
      { text: "Acme CRM", highlighted: true },
      { text: " and Rival.", highlighted: false },
    ]);
    expect(r.segments.map((s) => s.text).join("")).toBe(text);
    expect(r.ignoredSpans).toBe(0);
  });

  it("treats offsets as code points (Python str), not UTF-16 units", () => {
    // The emoji is one code point but two UTF-16 units: code points 4..8 are "Acme".
    const text = "\u{1F600} A Acme!";
    const r = buildSegments(text, [{ start: 4, end: 8 }]);
    expect(r.segments.find((s) => s.highlighted)?.text).toBe("Acme");
  });

  it("returns one plain segment when there are no spans", () => {
    expect(buildSegments("hello", []).segments).toEqual([{ text: "hello", highlighted: false }]);
  });

  it("ignores spans that do not fit the text instead of guessing", () => {
    const r = buildSegments("short", [
      { start: 2, end: 99 },
      { start: 3, end: 3 },
      { start: 4, end: 2 },
    ]);
    expect(r.segments).toEqual([{ text: "short", highlighted: false }]);
    expect(r.ignoredSpans).toBe(3);
  });

  it("drops an overlapping span and keeps the first", () => {
    const r = buildSegments("abcdefgh", [
      { start: 0, end: 4 },
      { start: 2, end: 6 },
    ]);
    expect(r.segments.filter((s) => s.highlighted).map((s) => s.text)).toEqual(["abcd"]);
    expect(r.ignoredSpans).toBe(1);
  });

  it("never interprets markup: html in the text stays literal text", () => {
    const text = "<img src=x onerror=alert(1)> Acme";
    const r = buildSegments(text, [{ start: 29, end: 33 }]);
    expect(r.segments[0]!.text).toBe("<img src=x onerror=alert(1)> ");
    expect(r.segments[1]).toEqual({ text: "Acme", highlighted: true });
  });
});
