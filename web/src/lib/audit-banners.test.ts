import { describe, expect, it } from "vitest";
import { makeAudit, makeRun } from "@/test/fixtures";
import { buildBanners } from "./audit-banners";

const ids = (b: ReturnType<typeof buildBanners>) => b.map((x) => x.id);

describe("buildBanners", () => {
  it("shows nothing for a clean complete audit", () => {
    expect(buildBanners(makeAudit(), [])).toEqual([]);
  });

  it("explains which limit stopped a capped audit and that results are partial", () => {
    const audit = makeAudit({ status: "capped", caps_hit: ["cost_cap", "fetch_cap"] }, { caps_hit: ["cost_cap", "fetch_cap"] });
    const [banner] = buildBanners(audit, []);
    expect(banner).toMatchObject({ id: "capped", tone: "warning" });
    expect(banner!.body).toContain("Cost cap");
    expect(banner!.body).toContain("Page fetch cap");
    expect(banner!.meaning).toMatch(/Only the runs that finished are counted/);
  });

  it.each([
    ["cost_cap", /Cost cap/],
    ["fetch_cap", /Page fetch cap/],
    ["domain_cap", /Per-domain cap/],
    ["max_steps", /Max steps/],
  ])("names %s", (cap, label) => {
    const audit = makeAudit({ status: "capped", caps_hit: [cap] }, { caps_hit: [cap] });
    expect(buildBanners(audit, [])[0]!.body).toMatch(label);
  });

  it("shows a degraded banner with the number of degraded runs", () => {
    const runs = [makeRun({ degraded: true }), makeRun({ id: "r2", degraded: true }), makeRun({ id: "r3" })];
    const b = buildBanners(makeAudit({ degraded: true }, { degraded: true }), runs).find((x) => x.id === "degraded");
    expect(b!.body).toContain("2 runs");
    expect(b!.meaning).toMatch(/fewer searched runs/);
  });

  it("shows a partial failure banner only when some runs failed", () => {
    const b = buildBanners(makeAudit({}, { failed_runs: 2, total_runs: 6 }), []).find((x) => x.id === "partial");
    expect(b!.title).toBe("2 runs failed");
    expect(b!.body).toContain("2 of 6");
    expect(buildBanners(makeAudit({}, { failed_runs: 0 }), []).some((x) => x.id === "partial")).toBe(false);
  });

  it("shows the failed banner for a failed audit and not also a partial one", () => {
    const ids_ = ids(buildBanners(makeAudit({ status: "failed" }, { failed_runs: 6, total_runs: 6 }), []));
    expect(ids_).toContain("failed");
    expect(ids_).not.toContain("partial");
  });

  it("links flagged mentions to the review queue filtered to this audit", () => {
    const b = buildBanners(makeAudit({ id: "abc" }, { flagged_count: 3 }), []).find((x) => x.id === "flagged");
    expect(b!.title).toBe("3 flagged mentions waiting for review");
    expect(b!.action!.href).toBe("/review?audit_id=abc");
    expect(b!.meaning).toMatch(/not counted/);
  });

  it("marks a running audit as provisional, and can show several banners together", () => {
    const audit = makeAudit({ status: "running", degraded: true }, { degraded: true, failed_runs: 1, flagged_count: 1 });
    expect(ids(buildBanners(audit, []))).toEqual(["degraded", "partial", "running", "flagged"]);
  });
});
