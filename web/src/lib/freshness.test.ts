import { describe, expect, it } from "vitest";
import { describeFreshness } from "./freshness";

const NOW = Date.parse("2026-03-10T12:00:00Z");

describe("describeFreshness", () => {
  it.each([
    ["2026-03-10T11:59:50Z", "just now", false],
    ["2026-03-10T11:59:00Z", "1 minute ago", false],
    ["2026-03-10T09:00:00Z", "3 hours ago", false],
    ["2026-03-09T11:00:00Z", "1 day ago", false],
    ["2026-03-03T12:00:00Z", "7 days ago", true],
    ["2026-01-01T12:00:00Z", "68 days ago", true],
  ])("%s -> %s (stale %s)", (iso, ago, stale) => {
    expect(describeFreshness(iso, NOW)).toEqual({ ago, stale });
  });

  it("unknown for missing, invalid or future timestamps", () => {
    expect(describeFreshness(null, NOW)).toBeNull();
    expect(describeFreshness("garbage", NOW)).toBeNull();
    expect(describeFreshness("2027-01-01T00:00:00Z", NOW)).toBeNull();
  });
});
