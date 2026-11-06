import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { FakeEventSource, step } from "@/test/fake-event-source";
import { makeAudit, makeRun, makeStability } from "@/test/fixtures";
import { json, renderWithClient } from "@/test/utils";
import { AuditDetailView } from "./audit-detail-view";

const fetchMock = vi.fn<typeof fetch>();
type Routes = { audit?: () => Response; runs?: () => Response };

function route(routes: Routes) {
  fetchMock.mockImplementation(async (input) => {
    const path = new URL((input as Request).url).pathname;
    if (path.endsWith("/runs")) return routes.runs?.() ?? json(200, []);
    return routes.audit?.() ?? json(404, { detail: "audit not found" });
  });
}
const auditCalls = () => fetchMock.mock.calls.filter((c) => !new URL((c[0] as Request).url).pathname.endsWith("/runs")).length;

beforeEach(() => {
  FakeEventSource.reset();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("EventSource", FakeEventSource);
});

const completeAudit = () =>
  makeAudit(
    {},
    {
      question_stability: [
        makeStability({ question_idx: 0, outcomes: [true, true, false], agreement: 0.67 }),
        makeStability({ question_idx: 1, outcomes: [true, true, true], agreement: 1 }),
      ],
    },
  );
const completeRuns = () => [
  makeRun({ id: "r-a", question_idx: 0, repeat_idx: 0 }),
  makeRun({ id: "r-b", question_idx: 0, repeat_idx: 1 }),
  makeRun({ id: "r-c", question_idx: 0, repeat_idx: 2, brand_mentioned: false }),
];

describe("AuditDetailView: complete audit", () => {
  it("shows a skeleton, then the scorecard and one row of dots per question", async () => {
    route({ audit: () => json(200, completeAudit()), runs: () => json(200, completeRuns()) });
    renderWithClient(<AuditDetailView auditId="aud-1" />);
    expect(screen.getByRole("status", { name: "Loading audit" })).toBeInTheDocument();

    expect(await screen.findByRole("heading", { level: 1, name: "Acme" })).toBeInTheDocument();
    const score = screen.getByRole("heading", { name: "Scorecard" }).closest("section")!;
    expect(within(score).getByTestId("metric-mention-rate")).toHaveTextContent("50%");
    expect(within(score).getByTestId("metric-avg-position")).toHaveTextContent("2.5");
    expect(within(score).getByTestId("metric-flagged")).toHaveTextContent("0");
    // Developer numbers (cost, latency, pages fetched) are not on the user's scorecard.
    for (const id of ["metric-pages", "metric-cost", "metric-latency"]) {
      expect(within(score).queryByTestId(id)).not.toBeInTheDocument();
    }
    const report = screen.getByRole("heading", { name: "Audit report" }).closest("section")!;
    expect(within(report).getByText(/Acme was mentioned in 50% of the AI answers/)).toBeInTheDocument();
    expect(within(report).getByRole("link", { name: /Sources and gap report/ })).toHaveAttribute(
      "href",
      "/sources?audit_id=aud-1",
    );
    expect(screen.queryByTestId("mode-delta")).not.toBeInTheDocument();

    const row0 = screen.getByTestId("question-row-0");
    expect(within(row0).getByText("Repeats disagree")).toBeInTheDocument();
    expect(within(row0).getByText(/2 of 3 mention the brand/)).toBeInTheDocument();
    expect(within(row0).getByText(/67% agreement/)).toBeInTheDocument();
    const dot1 = await within(row0).findByRole("link", { name: "Repeat 1: brand mentioned" });
    expect(dot1).toHaveAttribute("href", "/audits/aud-1/runs/r-a");
    expect(within(row0).getByRole("link", { name: "Repeat 3: brand not mentioned" })).toHaveAttribute(
      "href",
      "/audits/aud-1/runs/r-c",
    );
    const row1 = screen.getByTestId("question-row-1");
    expect(within(row1).queryByText("Repeats disagree")).not.toBeInTheDocument();
    expect(screen.queryByTestId("connection-state")).toHaveTextContent("Finished");
    expect(FakeEventSource.instances).toHaveLength(0); // nothing to stream for a finished audit
  });

  it("marks a failed repeat distinctly and does not call it a disagreement", async () => {
    const audit = makeAudit({}, { failed_runs: 1, question_stability: [makeStability({ outcomes: [true, null, true], agreement: 1 })] });
    route({ audit: () => json(200, audit) });
    renderWithClient(<AuditDetailView auditId="aud-1" />);
    const row = await screen.findByTestId("question-row-0");
    expect(within(row).getByRole("img", { name: "Repeat 2: no usable answer, the run failed" })).toBeInTheDocument();
    expect(within(row).queryByText("Repeats disagree")).not.toBeInTheDocument();
    expect(screen.getByTestId("banner-partial")).toHaveTextContent("1 run failed");
  });

  it("shows the grounded versus off delta for a both-mode audit", async () => {
    const audit = makeAudit({}, { mode_delta: 0.25, mention_rate_by_mode: { grounded: 0.75, off: 0.5 } });
    route({ audit: () => json(200, audit) });
    renderWithClient(<AuditDetailView auditId="aud-1" />);
    const card = await screen.findByTestId("mode-delta");
    expect(card).toHaveTextContent("75%");
    expect(card).toHaveTextContent("50%");
    expect(card).toHaveTextContent("+25 pts");
    expect(card).toHaveTextContent("higher with search");
  });
});

describe("AuditDetailView: banners", () => {
  it("explains a capped audit", async () => {
    route({ audit: () => json(200, makeAudit({ status: "capped", caps_hit: ["cost_cap"] }, { caps_hit: ["cost_cap"] })) });
    renderWithClient(<AuditDetailView auditId="aud-1" />);
    const banner = await screen.findByTestId("banner-capped");
    expect(banner).toHaveTextContent("results are partial");
    expect(banner).toHaveTextContent("Cost cap");
    expect(banner).toHaveTextContent("What it means for the numbers");
  });

  it("explains a degraded audit", async () => {
    route({
      audit: () => json(200, makeAudit({ degraded: true }, { degraded: true })),
      runs: () => json(200, [makeRun({ degraded: true })]),
    });
    renderWithClient(<AuditDetailView auditId="aud-1" />);
    expect(await screen.findByTestId("banner-degraded")).toHaveTextContent("without search");
  });

  it("shows the failed banner and dashes instead of 0%", async () => {
    route({ audit: () => json(200, makeAudit({ status: "failed" }, { mention_rate: 0, total_runs: 3, failed_runs: 3 })) });
    renderWithClient(<AuditDetailView auditId="aud-1" />);
    expect(await screen.findByTestId("banner-failed")).toHaveTextContent("No usable answers");
    expect(screen.getByTestId("metric-mention-rate")).toHaveTextContent("—");
    expect(screen.getByTestId("metric-mention-rate")).not.toHaveTextContent("0%");
  });

  it("links flagged mentions to the review queue for this audit", async () => {
    route({ audit: () => json(200, makeAudit({}, { flagged_count: 2 })) });
    renderWithClient(<AuditDetailView auditId="aud-1" />);
    const banner = await screen.findByTestId("banner-flagged");
    expect(banner).toHaveTextContent("2 flagged mentions waiting for review");
    expect(within(banner).getByRole("link")).toHaveAttribute("href", "/review?audit_id=aud-1");
  });
});

describe("AuditDetailView: other states", () => {
  it("shows a not-found state for a 404", async () => {
    route({ audit: () => json(404, { detail: "audit not found" }) });
    renderWithClient(<AuditDetailView auditId="nope" />);
    expect(await screen.findByRole("heading", { name: "Audit not found" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to all audits" })).toHaveAttribute("href", "/");
  });

  it("shows an error with a working retry", async () => {
    let n = 0;
    route({ audit: () => (++n <= 2 ? json(500, { detail: "database down" }) : json(200, completeAudit())) });
    renderWithClient(<AuditDetailView auditId="aud-1" />);
    const alert = await screen.findByRole("alert", undefined, { timeout: 4000 });
    expect(alert).toHaveTextContent("database down");
    await userEvent.setup().click(within(alert).getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Acme" })).toBeInTheDocument();
  });

  it("still shows the results, unlinked, when the runs fail to load", async () => {
    route({ audit: () => json(200, completeAudit()), runs: () => json(500, { detail: "runs broke" }) });
    renderWithClient(<AuditDetailView auditId="aud-1" />);
    expect(await screen.findByText(/runs broke/, undefined, { timeout: 4000 })).toBeInTheDocument();
    const row = screen.getByTestId("question-row-0");
    expect(within(row).getAllByRole("img", { name: /^Repeat/ })).toHaveLength(3);
    expect(within(row).queryByRole("link")).not.toBeInTheDocument();
  });
});

describe("AuditDetailView: running, live", () => {
  const running = () => makeAudit({ status: "running" }, null);

  it("shows the empty-ish state before any answers, with an indeterminate progress bar", async () => {
    route({ audit: () => json(200, running()) });
    renderWithClient(<AuditDetailView auditId="aud-1" />);
    expect(await screen.findByText(/Waiting for the first answers/)).toBeInTheDocument();
    const bar = screen.getByRole("progressbar", { name: "Audit progress" });
    expect(bar).not.toHaveAttribute("aria-valuenow");
    expect(screen.getByTestId("banner-running")).toBeInTheDocument();
    expect(screen.getByTestId("metric-mention-rate")).toHaveTextContent("—");
  });

  it("goes live, updates progress and refetches without polling, then finishes on a terminal event", async () => {
    let status: "running" | "complete" = "running";
    route({
      audit: () => json(200, status === "running" ? running() : completeAudit()),
      runs: () => json(200, []),
    });
    renderWithClient(<AuditDetailView auditId="aud-1" />);
    await screen.findByRole("progressbar");
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    expect(FakeEventSource.last.url).toBe("/api/audits/aud-1/events");
    expect(screen.getByTestId("connection-state")).toHaveTextContent("Connecting");

    act(() => FakeEventSource.last.open());
    expect(screen.getByTestId("connection-state")).toHaveTextContent("Live");

    const callsBefore = auditCalls();
    act(() => {
      FakeEventSource.last.emit("audit_started", 1, { planned_steps: 24 });
      for (let i = 0; i < 12; i++) FakeEventSource.last.emit("run_verified", 2 + i, { step: step(i, 0) });
    });
    expect(screen.getByTestId("progress-text")).toHaveTextContent("12 of 24 runs finished");
    const bar = screen.getByRole("progressbar", { name: "Audit progress" });
    expect(bar).toHaveAttribute("aria-valuenow", "12");
    expect(bar).toHaveAttribute("aria-valuemax", "24");
    expect(screen.getByTestId("progress-announcement")).toHaveAttribute("aria-live", "polite");
    expect(screen.getByTestId("progress-announcement")).toHaveTextContent(/of 24 runs finished/);
    await waitFor(() => expect(auditCalls()).toBeGreaterThan(callsBefore)); // an event caused a refetch

    status = "complete";
    act(() => FakeEventSource.last.emit("audit_completed", 20, { status: "complete" }));
    await waitFor(() => expect(screen.getByTestId("connection-state")).toHaveTextContent("Finished"));
    await waitFor(() => expect(screen.queryByRole("progressbar")).not.toBeInTheDocument());
    expect(FakeEventSource.last.closed).toBe(true);
    expect(await screen.findByTestId("question-row-0")).toBeInTheDocument();
  });

  it("shows reconnecting when the connection drops", async () => {
    route({ audit: () => json(200, running()) });
    renderWithClient(<AuditDetailView auditId="aud-1" />);
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    act(() => {
      FakeEventSource.last.open();
      FakeEventSource.last.fail({ closed: false });
    });
    expect(screen.getByTestId("connection-state")).toHaveTextContent("Reconnecting");
  });

  it("pads repeats that have not run yet with a distinct mark", async () => {
    const audit = makeAudit({ status: "running" }, { question_stability: [makeStability({ outcomes: [true], agreement: null })] });
    route({ audit: () => json(200, audit) });
    renderWithClient(<AuditDetailView auditId="aud-1" />);
    const row = await screen.findByTestId("question-row-0");
    expect(within(row).getByRole("img", { name: "Repeat 2: not run yet" })).toBeInTheDocument();
    expect(within(row).getByRole("img", { name: "Repeat 3: not run yet" })).toBeInTheDocument();
  });
});
