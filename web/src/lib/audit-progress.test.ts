import { describe, expect, it } from "vitest";
import { step } from "@/test/fake-event-source";
import {
  INITIAL_PROGRESS,
  announcementText,
  progressView,
  reduceProgress,
  type ProgressState,
} from "./audit-progress";
import type { StreamEvent } from "./audit-events";

const ev = (type: string, data: Record<string, unknown>): StreamEvent => ({ id: null, type, data });
const run = (state: ProgressState, events: StreamEvent[]) => events.reduce(reduceProgress, state);

describe("reduceProgress", () => {
  it("takes the planned total from audit_started or audit_resumed", () => {
    expect(run(INITIAL_PROGRESS, [ev("audit_started", { planned_steps: 12 })]).planned).toBe(12);
    expect(run(INITIAL_PROGRESS, [ev("audit_resumed", { planned_steps: 8 })]).planned).toBe(8);
  });

  it("counts a run once however many events it emitted or how often it is replayed", () => {
    const verified = ev("run_verified", { step: step(0, 0) });
    const s = run(INITIAL_PROGRESS, [
      ev("run_started", { step: step(0, 0) }),
      ev("answer_extracted", { step: step(0, 0) }),
      verified,
      verified,
    ]);
    expect(s.finished.size).toBe(1);
  });

  it("counts failed runs as finished and as failed", () => {
    const s = run(INITIAL_PROGRESS, [
      ev("run_verified", { step: step(0, 0) }),
      ev("run_failed", { step: step(0, 1), reason: "model_call_failed" }),
    ]);
    expect(progressView(s)).toMatchObject({ done: 2, failed: 1 });
  });

  it("ignores events without a usable step", () => {
    expect(run(INITIAL_PROGRESS, [ev("run_verified", {}), ev("run_failed", { step: "x" })]).finished.size).toBe(0);
  });
});

describe("progressView", () => {
  it("has an unknown fraction until the total is known", () => {
    const v = progressView(run(INITIAL_PROGRESS, [ev("run_verified", { step: step(0, 0) })]));
    expect(v.fraction).toBeNull();
    expect(v.text).toBe("1 runs finished");
  });
  it("reports 12 of 24", () => {
    let s = run(INITIAL_PROGRESS, [ev("audit_started", { planned_steps: 24 })]);
    for (let i = 0; i < 12; i++) s = reduceProgress(s, ev("run_verified", { step: step(i, 0) }));
    const v = progressView(s);
    expect(v.text).toBe("12 of 24 runs finished");
    expect(v.fraction).toBe(0.5);
  });
  it("uses finished run rows when they are ahead of the stream", () => {
    const s = run(INITIAL_PROGRESS, [ev("audit_started", { planned_steps: 10 })]);
    expect(progressView(s, 4).done).toBe(4);
  });
});

describe("announcementText", () => {
  it("only changes about every tenth of the run, and says the exact total at the end", () => {
    const texts = new Set(Array.from({ length: 25 }, (_, done) => announcementText(done, 24)));
    expect(texts.size).toBeLessThanOrEqual(11);
    expect(announcementText(24, 24)).toBe("24 of 24 runs finished");
    expect(announcementText(0, 24)).toBe("0 of 24 runs finished");
  });
  it("is silent until something finished when the total is unknown", () => {
    expect(announcementText(0, null)).toBe("");
  });
});
