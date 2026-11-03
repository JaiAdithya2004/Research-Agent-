import { describe, expect, it } from "vitest";
import { makeAudit } from "@/test/fixtures";
import { modeDeltaView, scorecardMetrics } from "./scorecard";

const metric = (audit: ReturnType<typeof makeAudit>, id: string) => scorecardMetrics(audit).find((m) => m.id === id)!;

describe("scorecardMetrics", () => {
  it("formats every metric", () => {
    const a = makeAudit();
    expect(metric(a, "mention-rate").value).toBe("50%");
    expect(metric(a, "avg-position").value).toBe("2.5");
    expect(metric(a, "flagged").value).toBe("0");
    expect(metric(a, "pages").value).toBe("7");
    expect(metric(a, "cost").value).toBe("$0.12");
    expect(metric(a, "latency").value).toBe("1.2 s / 3.4 s");
  });

  it("shows a dash, not 0%, when there are no usable answers", () => {
    const a = makeAudit({ status: "failed" }, { mention_rate: 0, total_runs: 4, failed_runs: 4, avg_position: null });
    expect(metric(a, "mention-rate").value).toBe("—");
    expect(metric(a, "avg-position").value).toBe("—");
    expect(metric(a, "latency").value).toBe("—");
  });

  it("shows dashes while a running audit has no summary yet", () => {
    const a = makeAudit({ status: "running" }, null);
    expect(scorecardMetrics(a).every((m) => m.value === "—")).toBe(true);
  });

  it("labels the headline mode", () => {
    expect(metric(makeAudit(), "mention-rate").label).toMatch(/with search/);
    const off = makeAudit();
    off.request.search_mode = "off";
    expect(metric(off, "mention-rate").label).toMatch(/no search/);
  });
});

describe("modeDeltaView", () => {
  it("is null unless the audit ran in both modes", () => {
    expect(modeDeltaView(makeAudit().summary)).toBeNull();
    expect(modeDeltaView(null)).toBeNull();
  });
  it("shows both rates and the signed delta", () => {
    const s = makeAudit({}, { mode_delta: 0.25, mention_rate_by_mode: { grounded: 0.75, off: 0.5 } }).summary;
    expect(modeDeltaView(s)).toMatchObject({ grounded: "75%", off: "50%", delta: "+25 pts", direction: "up" });
  });
  it("handles a negative and a zero delta", () => {
    const neg = makeAudit({}, { mode_delta: -0.33, mention_rate_by_mode: { grounded: 0.17, off: 0.5 } }).summary;
    expect(modeDeltaView(neg)).toMatchObject({ delta: "−33 pts", direction: "down" });
    const zero = makeAudit({}, { mode_delta: 0, mention_rate_by_mode: { grounded: 0.5, off: 0.5 } }).summary;
    expect(modeDeltaView(zero)).toMatchObject({ delta: "0 pts", direction: "flat" });
  });
});
