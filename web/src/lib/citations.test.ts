import { describe, expect, it } from "vitest";
import { summarizeCitations } from "./citations";

const cite = (over: Record<string, unknown>) => ({
  url: "https://a.com/x",
  domain: "a.com",
  title: null,
  source: "grounding_metadata" as const,
  unverified: false,
  evidence: null,
  ...over,
});
const ev = (fetch_status: string, brand_on_page: boolean | null) => ({
  source_id: "s",
  domain: "a.com",
  fetch_status,
  fetch_reason: "ok",
  brand_on_page,
  source_type: "other",
});

describe("summarizeCitations", () => {
  it("counts origin and page result separately, and keeps unreadable apart from not-found", () => {
    const s = summarizeCitations([
      cite({ evidence: ev("ok", true) }),
      cite({ evidence: ev("ok", false) }),
      cite({ evidence: ev("unreachable", null) }),
      cite({ source: "answer_text", unverified: true }),
    ] as never);
    expect(s).toMatchObject({ total: 4, grounded: 3, proseOnly: 1, found: 1, notFound: 1, unreadable: 1, notResearched: 1, partial: true });
  });

  it("is not partial when every page was read", () => {
    expect(summarizeCitations([cite({ evidence: ev("ok", true) })] as never).partial).toBe(false);
    expect(summarizeCitations([]).partial).toBe(false);
  });
});
