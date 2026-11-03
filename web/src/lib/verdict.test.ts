import { describe, expect, it } from "vitest";
import { presentVerdict } from "./verdict";

const ok = { status: "ok" as const, error: null, verdict: null, review: null, brand_mentioned: null as boolean | null };

describe("presentVerdict", () => {
  it("verified", () => {
    const v = presentVerdict({ ...ok, verdict: { status: "verified", reason: "quote found" }, brand_mentioned: true });
    expect(v).toMatchObject({ kind: "verified", label: "Verified", tone: "success" });
    expect(v.detail).toBe("quote found");
  });

  it("flagged without a review is waiting for a human and says so", () => {
    const v = presentVerdict({ ...ok, verdict: { status: "flagged", reason: "quote not in answer" } });
    expect(v).toMatchObject({ kind: "flagged", label: "Flagged: needs review", tone: "warning" });
  });

  it("a review wins over the verdict and shows who decided and why", () => {
    const v = presentVerdict({
      ...ok,
      verdict: { status: "flagged", reason: "x" },
      review: { action: "correct", corrected_value: "position=2", reason: "it is second", reviewer: "dev", timestamp: "2026-03-01T10:00:00Z" },
    });
    expect(v.kind).toBe("reviewed");
    expect(v.label).toBe("Corrected by a reviewer");
    expect(v.detail).toContain("Position 2");
    expect(v.detail).toContain("it is second");
    expect(v.detail).toContain("dev");
  });

  it("approve and reject have their own labels", () => {
    const r = (action: "approve" | "reject") =>
      presentVerdict({ ...ok, review: { action, corrected_value: null, reason: "r", reviewer: "dev", timestamp: "2026-03-01T10:00:00Z" } });
    expect(r("approve").label).toBe("Approved by a reviewer");
    expect(r("reject").label).toBe("Rejected by a reviewer");
  });

  it("a failed run has no verdict at all", () => {
    expect(presentVerdict({ ...ok, status: "failed", error: "timeout" })).toMatchObject({ kind: "failed", label: "No usable answer" });
  });

  it("a pending run is not a verdict either", () => {
    expect(presentVerdict({ ...ok, status: "pending" }).kind).toBe("pending");
  });

  it("an ok run with no verdict is 'not checked', never 'verified'", () => {
    expect(presentVerdict(ok).kind).toBe("unchecked");
  });
});
