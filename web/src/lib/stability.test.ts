import { describe, expect, it } from "vitest";
import { makeRun, makeStability } from "@/test/fixtures";
import { agreementText, buildQuestionRows, modeLabel } from "./stability";

const opts = { repeats: 3, running: false };

describe("buildQuestionRows", () => {
  it("draws one row per question and one dot per repeat, with accessible labels", () => {
    const rows = buildQuestionRows(["Q1", "Q2"], [makeStability({ outcomes: [true, false, null] })], [], opts);
    expect(rows).toHaveLength(2);
    const dots = rows[0]!.groups[0]!.dots;
    expect(dots.map((d) => d.kind)).toEqual(["mentioned", "absent", "failed"]);
    expect(dots.map((d) => d.label)).toEqual([
      "Repeat 1: brand mentioned",
      "Repeat 2: brand not mentioned",
      "Repeat 3: no usable answer, the run failed",
    ]);
    expect(rows[1]!.groups).toEqual([]); // nothing ran for question 2
  });

  it("flags disagreement between usable repeats and keeps the agreement number", () => {
    const [row] = buildQuestionRows(["Q1"], [makeStability({ outcomes: [true, true, false], agreement: 0.67 })], [], opts);
    expect(row!.disagree).toBe(true);
    expect(row!.groups[0]).toMatchObject({ disagree: true, agreement: 0.67, mentioned: 2, usable: 3 });
  });

  it("does not call a failed repeat a disagreement", () => {
    const [row] = buildQuestionRows(["Q1"], [makeStability({ outcomes: [true, null, true], agreement: 1 })], [], opts);
    expect(row!.disagree).toBe(false);
  });

  it("agreeing absent repeats do not disagree", () => {
    const [row] = buildQuestionRows(["Q1"], [makeStability({ outcomes: [false, false, false], agreement: 1 })], [], opts);
    expect(row!.disagree).toBe(false);
  });

  it("links each dot to its run by repeat order", () => {
    const runs = [
      makeRun({ id: "r2", repeat_idx: 1 }),
      makeRun({ id: "r1", repeat_idx: 0 }),
      makeRun({ id: "r3", repeat_idx: 2 }),
      makeRun({ id: "other", question_idx: 1 }),
    ];
    const [row] = buildQuestionRows(["Q1"], [makeStability()], runs, opts);
    expect(row!.groups[0]!.dots.map((d) => d.runId)).toEqual(["r1", "r2", "r3"]);
  });

  it("pads missing repeats as not run, worded by whether the audit is still running", () => {
    const stability = [makeStability({ outcomes: [true], agreement: null })];
    const running = buildQuestionRows(["Q1"], stability, [], { repeats: 3, running: true })[0]!.groups[0]!.dots;
    expect(running.map((d) => d.kind)).toEqual(["mentioned", "pending", "pending"]);
    expect(running[1]!.label).toBe("Repeat 2: not run yet");
    expect(running[1]!.runId).toBeNull();
    const stopped = buildQuestionRows(["Q1"], stability, [], { repeats: 3, running: false })[0]!.groups[0]!.dots;
    expect(stopped[1]!.label).toBe("Repeat 2: not run, the audit stopped before it");
  });

  it("keeps grounded and off groups apart, grounded first", () => {
    const [row] = buildQuestionRows(
      ["Q1"],
      [makeStability({ search_mode: "off", outcomes: [false, false, false], agreement: 1 }), makeStability()],
      [],
      opts,
    );
    expect(row!.groups.map((g) => g.modeLabel)).toEqual(["with search", "without search"]);
  });
});

describe("modeLabel", () => {
  it("names all three ways a run can search", () => {
    expect(modeLabel("grounded")).toBe("with search");
    expect(modeLabel("tavily")).toBe("with Tavily search");
    expect(modeLabel("off")).toBe("without search");
  });

  it("orders groups grounded, tavily, off", () => {
    const [row] = buildQuestionRows(
      ["Q1"],
      [
        makeStability({ search_mode: "off", outcomes: [false], agreement: null }),
        makeStability({ search_mode: "tavily", outcomes: [true], agreement: null }),
        makeStability(),
      ],
      [],
      opts,
    );
    expect(row!.groups.map((g) => g.modeLabel)).toEqual(["with search", "with Tavily search", "without search"]);
  });
});

describe("agreementText", () => {
  it("explains a missing agreement instead of showing 0%", () => {
    expect(agreementText({ agreement: null, usable: 1, mentioned: 1 })).toMatch(/needs 2 or more/);
    expect(agreementText({ agreement: 0.67, usable: 3, mentioned: 2 })).toBe("67% agreement");
  });
});
