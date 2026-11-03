import { describe, expect, it } from "vitest";
import { hasFilters, parseFilters, serializeFilters } from "./audit-filters";

describe("audit filters in the query string", () => {
  it("parses a valid status and brand", () => {
    expect(parseFilters(new URLSearchParams("status=capped&brand=%20Acme%20"))).toEqual({ status: "capped", brand: "Acme" });
  });
  it("ignores an unknown status", () => {
    expect(parseFilters(new URLSearchParams("status=bogus"))).toEqual({ status: null, brand: "" });
  });
  it("limits the brand to 255 characters like the API", () => {
    expect(parseFilters(new URLSearchParams(`brand=${"a".repeat(300)}`)).brand).toHaveLength(255);
  });
  it("round-trips", () => {
    const f = { status: "running" as const, brand: "A&B Co" };
    expect(parseFilters(new URLSearchParams(serializeFilters(f)))).toEqual(f);
  });
  it("serializes to an empty string with no filters", () => {
    expect(serializeFilters({ status: null, brand: "  " })).toBe("");
    expect(hasFilters({ status: null, brand: " " })).toBe(false);
    expect(hasFilters({ status: "failed", brand: "" })).toBe(true);
  });
});
