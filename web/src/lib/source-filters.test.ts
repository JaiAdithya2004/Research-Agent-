import { describe, expect, it } from "vitest";
import { hasSourceFilters, parseSourcesParams, serializeSourcesParams } from "./source-filters";

const p = (s: string) => parseSourcesParams(new URLSearchParams(s));

describe("source params", () => {
  it("parses every field", () => {
    expect(p("audit_id=a1&source_type=review&fetch_status=unreachable&domain=x.com&source=s1")).toEqual({
      auditId: "a1",
      sourceType: "review",
      fetchStatus: "unreachable",
      domain: "x.com",
      sourceId: "s1",
    });
  });

  it("drops unknown enum values instead of passing them to the API", () => {
    const r = p("source_type=bogus&fetch_status=nope");
    expect(r.sourceType).toBeNull();
    expect(r.fetchStatus).toBeNull();
  });

  it("round-trips and omits empty values", () => {
    expect(serializeSourcesParams(p(""))).toBe("");
    const q = "?audit_id=a1&source_type=news&fetch_status=ok&domain=x.com&source=s1";
    expect(serializeSourcesParams(p(q))).toBe(q);
  });

  it("hasSourceFilters ignores the audit and the open source", () => {
    expect(hasSourceFilters(p("audit_id=a1&source=s1"))).toBe(false);
    expect(hasSourceFilters(p("domain=x.com"))).toBe(true);
    expect(hasSourceFilters(p("fetch_status=blocked"))).toBe(true);
  });

  it("trims and caps the domain filter at the API's max length", () => {
    expect(p("domain=" + "a".repeat(400)).domain).toHaveLength(255);
    expect(p("domain=%20%20").domain).toBe("");
  });
});
