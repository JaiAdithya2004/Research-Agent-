import { describe, expect, it } from "vitest";
import { ASSUMPTIONS, estimateAudit, type EstimateInput } from "./estimate";

const base: EstimateInput = { questions: 1, repeats: 1, searchMode: "grounded", researchDepth: "cited_pages" };

describe("estimateAudit", () => {
  it("counts runs as questions x repeats x 2 models and prices each part", () => {
    const e = estimateAudit(base);
    expect(e.runs).toBe(2);
    expect(e.groundedRuns).toBe(2);
    expect(e.modelCalls).toEqual({ count: 2, usd: 0.00825 });
    expect(e.extractionCalls).toEqual({ count: 2, usd: 0.002925 });
    expect(e.searchCalls).toEqual({ count: 6, usd: 0.084 });
    expect(e.pageFetches.count).toBe(6);
    expect(e.classificationCalls).toEqual({ count: 6, usd: 0.008775 });
    expect(e.totalUsd).toBe(0.10395);
    expect(e.capUsd).toBe(0.75);
    expect(e.exceedsCap).toBe(false);
  });

  it("doubles the runs in both mode but only half of them search", () => {
    const e = estimateAudit({ ...base, searchMode: "both" });
    expect(e.runs).toBe(4);
    expect(e.groundedRuns).toBe(2);
    expect(e.searchCalls.count).toBe(6);
    expect(e.modelCalls.count).toBe(4);
    expect(e.extractionCalls.count).toBe(4);
    expect(e.pageFetches.count).toBe(6);
  });

  it("off mode has no search calls and no page fetches", () => {
    const e = estimateAudit({ ...base, searchMode: "off" });
    expect(e.runs).toBe(2);
    expect(e.groundedRuns).toBe(0);
    expect(e.searchCalls).toEqual({ count: 0, usd: 0 });
    expect(e.pageFetches.count).toBe(0);
    expect(e.classificationCalls.usd).toBe(0);
  });

  it("research off removes page fetches and classification but keeps search", () => {
    const e = estimateAudit({ ...base, researchDepth: "none" });
    expect(e.pageFetches.count).toBe(0);
    expect(e.classificationCalls).toEqual({ count: 0, usd: 0 });
    expect(e.searchCalls.count).toBe(6);
  });

  it("caps page fetches at 30", () => {
    expect(estimateAudit({ ...base, questions: 5, repeats: 3 }).pageFetches.count).toBe(30);
    expect(estimateAudit({ ...base, questions: 5, repeats: 1 }).pageFetches.count).toBe(30); // 3 x 10 grounded runs
    expect(estimateAudit({ ...base, questions: 4, repeats: 1 }).pageFetches.count).toBe(24);
    expect(ASSUMPTIONS.maxPageFetches).toBe(30);
  });

  it("warns above the $0.75 cap and not at or below it", () => {
    const two = estimateAudit({ ...base, questions: 2, repeats: 3, researchDepth: "none" });
    const three = estimateAudit({ ...base, questions: 3, repeats: 3, researchDepth: "none" });
    expect(two.totalUsd).toBe(0.57105);
    expect(two.exceedsCap).toBe(false);
    expect(three.totalUsd).toBe(0.856575);
    expect(three.exceedsCap).toBe(true);
  });

  it("handles empty input and the largest form", () => {
    const empty = estimateAudit({ ...base, questions: 0 });
    expect(empty.runs).toBe(0);
    expect(empty.totalUsd).toBe(0);
    const max = estimateAudit({ questions: 10, repeats: 5, searchMode: "both", researchDepth: "cited_pages" });
    expect(max.runs).toBe(200);
    expect(max.groundedRuns).toBe(100);
    expect(max.searchCalls.count).toBe(300);
    expect(max.pageFetches.count).toBe(30);
    expect(max.exceedsCap).toBe(true);
  });
});
