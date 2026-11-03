import { describe, expect, it } from "vitest";
import { locateQuote, snapshotView } from "./snapshot";

describe("locateQuote", () => {
  it("finds an exact whole-word quote", () => {
    const t = "We love Acme CRM for startups.";
    expect(locateQuote(t, "Acme CRM")).toEqual({ start: 8, end: 16 });
  });

  it("ignores case, spacing and punctuation like the backend", () => {
    const t = "We love acme   crm, for startups.";
    const span = locateQuote(t, "Acme-CRM");
    expect(span && t.slice(span.start, span.end)).toBe("acme   crm");
  });

  it("does not match inside a longer word", () => {
    expect(locateQuote("Acmeville is a town", "Acme")).toBeNull();
  });

  it("returns null for no quote, empty text or an absent quote", () => {
    expect(locateQuote("text", null)).toBeNull();
    expect(locateQuote("", "x")).toBeNull();
    expect(locateQuote("some text", "Acme")).toBeNull();
  });

  it("treats regex characters in the quote literally", () => {
    const t = "Pay (Acme+) now";
    expect(locateQuote(t, "(Acme+)")).toEqual({ start: 4, end: 11 });
  });
});

describe("snapshotView", () => {
  it("highlights the quote and keeps all the text", () => {
    const v = snapshotView("a b Acme c", "Acme", false);
    expect(v.segments).toEqual([
      { text: "a b ", highlighted: false },
      { text: "Acme", highlighted: true },
      { text: " c", highlighted: false },
    ]);
    expect(v.truncated).toBe(false);
    expect(v.located).toBe(true);
  });

  it("truncates long text at the cap and says how much is hidden", () => {
    const v = snapshotView("x".repeat(100), null, false, 40);
    expect(v.shown).toBe(40);
    expect(v.total).toBe(100);
    expect(v.truncated).toBe(true);
    expect(v.segments.map((s) => s.text).join("")).toHaveLength(40);
    expect(snapshotView("x".repeat(100), null, true, 40).truncated).toBe(false);
  });

  it("shows everything when the quote lies beyond the cap, so the highlight is never hidden", () => {
    const text = "x".repeat(100) + " Acme";
    const v = snapshotView(text, "Acme", false, 40);
    expect(v.truncated).toBe(false);
    expect(v.segments.some((s) => s.highlighted)).toBe(true);
  });

  it("does not cut an emoji in half", () => {
    const text = "ab\u{1F600}cd"; // the emoji is two UTF-16 units at index 2-3
    const v = snapshotView(text, null, false, 3);
    expect(v.segments[0]!.text).toBe("ab");
  });

  it("reports an unlocated quote without inventing a highlight", () => {
    const v = snapshotView("nothing here", "Acme", false);
    expect(v.located).toBe(false);
    expect(v.segments).toEqual([{ text: "nothing here", highlighted: false }]);
  });
});
