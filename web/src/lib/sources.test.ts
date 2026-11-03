import { describe, expect, it } from "vitest";
import { limitRows, summarizeSources } from "./sources";

const src = (fetch_status: string, brand_on_page: boolean | null) => ({ fetch_status, brand_on_page, fetch_reason: "x" }) as never;

describe("summarizeSources", () => {
  it("keeps unreadable pages out of 'not found'", () => {
    expect(
      summarizeSources([src("ok", true), src("ok", false), src("unreachable", null), src("paywalled", null), src("blocked", false)]),
    ).toEqual({ total: 5, found: 1, notFound: 1, unreadable: 3 });
  });
});

describe("limitRows", () => {
  const rows = Array.from({ length: 12 }, (_, i) => i);
  it("caps and reports what is hidden", () => {
    expect(limitRows(rows, false, 5)).toEqual({ shown: [0, 1, 2, 3, 4], hidden: 7 });
    expect(limitRows(rows, true, 5).hidden).toBe(0);
    expect(limitRows(rows.slice(0, 3), false, 5).hidden).toBe(0);
  });
});
