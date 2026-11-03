import { describe, expect, it } from "vitest";
import { citationOrigin, presentEvidence } from "./evidence";

const base = { fetch_reason: "ok" };

describe("presentEvidence", () => {
  it("brand found on a page that was read", () => {
    const p = presentEvidence({ ...base, fetch_status: "ok", brand_on_page: true });
    expect(p.kind).toBe("found");
    expect(p.label).toBe("Brand found on page");
  });

  it("brand not found only when the page was actually read", () => {
    const p = presentEvidence({ ...base, fetch_status: "ok", brand_on_page: false });
    expect(p.kind).toBe("not_found");
    expect(p.label).toBe("Page read: brand not on it");
  });

  it.each(["unreachable", "blocked", "paywalled", "too_large", "error"] as const)(
    "%s with brand_on_page null is unreadable, never 'brand not found'",
    (status) => {
      const p = presentEvidence({ fetch_status: status, fetch_reason: "timeout", brand_on_page: null });
      expect(p.kind).toBe("unreadable");
      expect(p.label).toMatch(/^Could not read/);
      expect(p.label).not.toMatch(/not on it/i);
    },
  );

  it("never says 'not found' for an unreadable page even if the data claims brand_on_page false", () => {
    expect(presentEvidence({ ...base, fetch_status: "unreachable", brand_on_page: false }).kind).toBe("unreadable");
  });

  it("uses distinct icon, tone and label for unreadable and not found", () => {
    const a = presentEvidence({ ...base, fetch_status: "ok", brand_on_page: false });
    const b = presentEvidence({ ...base, fetch_status: "unreachable", brand_on_page: null });
    expect(a.icon).not.toBe(b.icon);
    expect(a.tone).not.toBe(b.tone);
    expect(a.label).not.toBe(b.label);
  });

  it("null evidence means the page was never researched", () => {
    expect(presentEvidence(null).kind).toBe("not_researched");
  });

  it("shows a friendly reason, and the raw code when unknown", () => {
    expect(
      presentEvidence({ fetch_status: "blocked", fetch_reason: "robots_disallowed", brand_on_page: null }).reason,
    ).toMatch(/robots/);
    expect(presentEvidence({ fetch_status: "error", fetch_reason: "weird_code", brand_on_page: null }).reason).toBe(
      "weird_code",
    );
  });
});

describe("citationOrigin", () => {
  it("grounding metadata is trusted", () => {
    expect(citationOrigin({ source: "grounding_metadata", unverified: false }).kind).toBe("grounding");
  });
  it("answer_text or unverified is prose-only", () => {
    expect(citationOrigin({ source: "answer_text", unverified: true }).kind).toBe("prose");
    expect(citationOrigin({ source: "grounding_metadata", unverified: true }).kind).toBe("prose");
  });
});
