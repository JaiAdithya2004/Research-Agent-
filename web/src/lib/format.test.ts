import { describe, expect, it } from "vitest";
import { capExplanation, capLabel, presentStatus } from "./audit-status";
import { formatRate, formatSignedPoints, formatUsd } from "./format";

describe("format", () => {
  it("shows a missing rate as a dash, never 0%", () => {
    expect(formatRate(null)).toBe("—");
    expect(formatRate(0)).toBe("0%");
    expect(formatRate(0.4166)).toBe("42%");
  });
  it("formats cost", () => {
    expect(formatUsd(0)).toBe("$0.00");
    expect(formatUsd(0.0412)).toBe("$0.041");
    expect(formatUsd(0.7)).toBe("$0.70");
  });
  it("signs the delta", () => {
    expect(formatSignedPoints(0.25)).toBe("+25 pts");
    expect(formatSignedPoints(-0.5)).toBe("−50 pts");
    expect(formatSignedPoints(0)).toBe("0 pts");
  });
});

describe("audit status", () => {
  it("gives each status its own label and icon", () => {
    const all = (["running", "complete", "capped", "failed"] as const).map(presentStatus);
    expect(new Set(all.map((s) => s.label)).size).toBe(4);
    expect(new Set(all.map((s) => s.icon)).size).toBe(4);
  });
  it("labels known caps and falls back to the raw code", () => {
    expect(capLabel("cost_cap")).toBe("Cost cap");
    expect(capLabel("something_new")).toBe("something_new");
    expect(capExplanation("fetch_cap")).toMatch(/30/);
  });
});

describe("formatShortDate", () => {
  it("formats a month and day, and a dash for garbage", async () => {
    const { formatShortDate } = await import("./format");
    expect(formatShortDate(Date.UTC(2026, 2, 1, 12))).toBe("Mar 1");
    expect(formatShortDate(Number.NaN)).toBe("—");
  });
});
