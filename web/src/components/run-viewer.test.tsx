import { act, cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { json, renderWithApp, runDetail } from "@/test/utils";
import { RunViewer } from "./run-viewer";

const fetchMock = vi.fn<typeof fetch>();

function evidence(over: Record<string, unknown>) {
  return { source_id: "src-1", domain: "a.com", fetch_status: "ok", fetch_reason: "ok", brand_on_page: true, source_type: "review", ...over };
}
function citation(over: Record<string, unknown>) {
  return {
    url: "https://a.com/page",
    domain: "a.com",
    title: "A page",
    source: "grounding_metadata",
    unverified: false,
    evidence: evidence({}),
    ...over,
  };
}

/** Answers GET /runs/..., GET /audits/..., and records review POSTs. */
function route(run: unknown, auditOver: Record<string, unknown> = {}) {
  fetchMock.mockImplementation(async (input) => {
    const req = input as Request;
    const path = new URL(req.url).pathname;
    if (req.method === "POST" && path.endsWith("/review")) return json(201, { id: "rev-1" });
    if (path.startsWith("/api/runs/")) return json(200, run);
    if (path.startsWith("/api/audits/"))
      return json(200, {
        id: "aud-1",
        status: "complete",
        brand: "Acme",
        created_at: "2026-03-01T10:00:00Z",
        completed_at: null,
        degraded: false,
        caps_hit: [],
        request: { brand: "Acme", aliases: [], category: "crm", competitors: [], questions: ["Q"], repeats: 1, search_mode: "grounded", research_depth: "cited_pages" },
        summary: null,
        ...auditOver,
      });
    return json(404, { detail: "nope" });
  });
}
const posts = () => fetchMock.mock.calls.filter(([r]) => (r as Request).method === "POST");

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  window.localStorage.clear();
  window.sessionStorage.clear();
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("RunViewer: answer and highlight", () => {
  it("shows the raw answer with the quote highlighted in place, and a verdict chip", async () => {
    route(runDetail());
    renderWithApp(<RunViewer auditId="aud-1" runId="run-1" />);
    expect(screen.getByRole("status", { name: "Loading answer" })).toBeInTheDocument();
    const region = await screen.findByRole("region", { name: "Answer text" });
    const mark = within(region).getByTestId("quote-highlight");
    expect(mark).toHaveTextContent("Acme CRM");
    expect(region.textContent).toContain("Top picks: ");
    expect(region.textContent).toContain(" and Rival. See https://a.com.");
    expect(screen.getByText("Verified")).toBeInTheDocument();
  });

  it("renders markup in the answer as text, never as HTML", async () => {
    const evil = '<img src=x onerror="alert(1)"> <script>alert(2)</script> Acme';
    route(runDetail({ raw_answer: evil, highlights: [{ start: 53, end: 57 }], mention: { ...runDetail().mention, quote: "Acme" } }));
    const { container } = renderWithApp(<RunViewer auditId="aud-1" runId="run-1" />);
    const region = await screen.findByRole("region", { name: "Answer text" });
    expect(region.textContent).toContain("<img src=x");
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
  });

  it("says so when the quote could not be placed, instead of highlighting something else", async () => {
    route(runDetail({ highlights: [], verdict: { status: "flagged", reason: "quote not in answer" } }));
    renderWithApp(<RunViewer auditId="aud-1" runId="run-1" />);
    await screen.findByRole("region", { name: "Answer text" });
    expect(screen.queryByTestId("quote-highlight")).not.toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent(/could not be placed/);
    expect(screen.getByText("Flagged: needs review")).toBeInTheDocument();
  });
});

describe("RunViewer: citations", () => {
  const citations = [
    citation({ url: "https://found.com/x", domain: "found.com", evidence: evidence({ source_id: "s1", domain: "found.com", brand_on_page: true }) }),
    citation({ url: "https://absent.com/x", domain: "absent.com", evidence: evidence({ source_id: "s2", domain: "absent.com", brand_on_page: false }) }),
    citation({
      url: "https://down.com/x",
      domain: "down.com",
      evidence: evidence({ source_id: "s3", domain: "down.com", fetch_status: "unreachable", fetch_reason: "timeout", brand_on_page: null }),
    }),
    citation({ url: "https://prose.com/x", domain: "prose.com", source: "answer_text", unverified: true, evidence: null }),
    citation({ url: "javascript:alert(1)", domain: "evil", source: "answer_text", unverified: true, evidence: null }),
  ];

  it("shows domain, origin, fetch status and brand-on-page for every citation", async () => {
    route(runDetail({ citations }));
    renderWithApp(<RunViewer auditId="aud-1" runId="run-1" />);
    const items = await screen.findAllByTestId("citation");
    expect(items).toHaveLength(5);
    const [found, absent, down, prose] = items;
    expect(within(found!).getByText("found.com")).toBeInTheDocument();
    expect(within(found!).getByText("From grounding metadata")).toBeInTheDocument();
    expect(within(found!).getByText("Fetched")).toBeInTheDocument();
    expect(within(found!).getByText("Brand found on page")).toBeInTheDocument();
    expect(within(absent!).getByText("Page read: brand not on it")).toBeInTheDocument();
    expect(within(prose!).getByText("Only in answer text (unverified)")).toBeInTheDocument();
    expect(within(prose!).getByText("Page not checked")).toBeInTheDocument();
    expect(within(down!).getByText("Unreachable")).toBeInTheDocument();
  });

  it("makes an unreachable page look and read differently from 'brand not found'", async () => {
    route(runDetail({ citations }));
    renderWithApp(<RunViewer auditId="aud-1" runId="run-1" />);
    const items = await screen.findAllByTestId("citation");
    const absent = items[1]!;
    const down = items[2]!;
    expect(within(down).getByText("Could not read: unreachable")).toBeInTheDocument();
    expect(within(down).queryByText(/brand not on it/)).not.toBeInTheDocument();
    expect(within(down).getByText(/took too long/)).toBeInTheDocument();
    expect(down.querySelector('[data-evidence-kind="unreadable"]')).not.toBeNull();
    expect(absent.querySelector('[data-evidence-kind="not_found"]')).not.toBeNull();
    expect(absent.querySelector('[data-evidence-kind="unreadable"]')).toBeNull();
    // A separate warning explains that unreadable pages are not "brand absent".
    expect(screen.getByText("Some pages could not be read")).toBeInTheDocument();
    expect(screen.getByTestId("citation-summary")).toHaveTextContent(/1 could not be read/);
  });

  it("only links http(s) URLs", async () => {
    route(runDetail({ citations }));
    renderWithApp(<RunViewer auditId="aud-1" runId="run-1" />);
    const items = await screen.findAllByTestId("citation");
    expect(within(items[0]!).getByRole("link", { name: /found.com\/x/ })).toHaveAttribute("href", "https://found.com/x");
    expect(within(items[4]!).queryByRole("link", { name: /javascript/ })).not.toBeInTheDocument();
    expect(within(items[4]!).getByText(/shown as text only/)).toBeInTheDocument();
  });

  it("links to the page snapshot only when the page was researched", async () => {
    route(runDetail({ citations }));
    renderWithApp(<RunViewer auditId="aud-1" runId="run-1" />);
    const items = await screen.findAllByTestId("citation");
    expect(within(items[0]!).getByRole("link", { name: "Open the page snapshot" })).toHaveAttribute("href", "/sources?audit_id=aud-1&source=s1");
    expect(within(items[3]!).queryByRole("link", { name: "Open the page snapshot" })).not.toBeInTheDocument();
  });

  it("explains an empty citation list differently when search was off", async () => {
    route(runDetail({ citations: [], search_mode: "off" }));
    renderWithApp(<RunViewer auditId="aud-1" runId="run-1" />);
    expect(await screen.findByText(/Search was off for this run/)).toBeInTheDocument();
  });

  it("tells the user when the fetch cap left pages unchecked", async () => {
    route(runDetail({ citations: [citation({ evidence: null })] }), { status: "capped", caps_hit: ["fetch_cap"] });
    renderWithApp(<RunViewer auditId="aud-1" runId="run-1" />);
    expect(await screen.findByText("The page fetch cap was reached")).toBeInTheDocument();
    expect(screen.getByText("This audit stopped fetching pages at a limit")).toBeInTheDocument();
  });
});

describe("RunViewer: states", () => {
  it("not found", async () => {
    fetchMock.mockResolvedValue(json(404, { detail: "run not found" }));
    renderWithApp(<RunViewer auditId="aud-1" runId="nope" />);
    expect(await screen.findByRole("heading", { name: "Answer not found" })).toBeInTheDocument();
  });

  it("error with retry", async () => {
    fetchMock.mockResolvedValueOnce(json(500, { detail: "boom" }));
    fetchMock.mockResolvedValueOnce(json(500, { detail: "boom" }));
    renderWithApp(<RunViewer auditId="aud-1" runId="run-1" />);
    expect(await screen.findByRole("alert", {}, { timeout: 4000 })).toHaveTextContent("boom"); // one automatic retry (1 s) first
    route(runDetail());
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("region", { name: "Answer text" })).toBeInTheDocument();
  });

  it("failed run: no answer, no verdict", async () => {
    route(runDetail({ status: "failed", error: "provider timeout", raw_answer: null, mention: null, verdict: null, brand_mentioned: null }));
    renderWithApp(<RunViewer auditId="aud-1" runId="run-1" />);
    expect(await screen.findByText("This run produced no usable answer")).toBeInTheDocument();
    expect(screen.getAllByText(/provider timeout/).length).toBeGreaterThan(0);
    expect(screen.queryByRole("region", { name: "Answer text" })).not.toBeInTheDocument();
    expect(screen.queryByRole("form")).not.toBeInTheDocument();
  });

  it("degraded run", async () => {
    route(runDetail({ degraded: true, search_mode: "off" }));
    renderWithApp(<RunViewer auditId="aud-1" runId="run-1" />);
    expect(await screen.findByText("Degraded run")).toBeInTheDocument();
  });

  it("pending run", async () => {
    route(runDetail({ status: "pending", raw_answer: null, mention: null, verdict: null }));
    renderWithApp(<RunViewer auditId="aud-1" runId="run-1" />);
    expect(await screen.findByText("This run has not finished yet")).toBeInTheDocument();
  });
});

describe("RunViewer: review with undo", () => {
  async function setup() {
    route(runDetail({ verdict: { status: "flagged", reason: "quote not in answer" } }));
    renderWithApp(<RunViewer auditId="aud-1" runId="run-1" />);
    await screen.findByRole("form", { name: /Review:/ });
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"] });
    // Testing Library only advances fake timers for asyncWrapper when it sees a `jest` global.
    vi.stubGlobal("jest", { advanceTimersByTime: (ms: number) => vi.advanceTimersByTime(ms) });
    return userEvent.setup({ advanceTimers: (ms) => vi.advanceTimersByTime(ms) });
  }

  it("refuses an empty reason for every action", async () => {
    const user = await setup();
    for (const name of ["Approve", "Correct", "Reject"]) {
      await user.click(screen.getByRole("radio", { name }));
      await user.click(screen.getByRole("button", { name }));
      expect(screen.getByText(/Give a reason/)).toBeInTheDocument();
    }
    expect(posts()).toHaveLength(0);
    expect(screen.queryByText(/queued/)).not.toBeInTheDocument();
  });

  it("queues the decision, shows an undo notice, and POSTs only after 5 seconds, without a reviewer field", async () => {
    const user = await setup();
    await user.type(screen.getByLabelText(/Reason/), "Quote matches the text");
    await user.click(screen.getByRole("button", { name: "Approve" }));

    expect(screen.getByText("Approve queued")).toBeInTheDocument();
    expect(screen.getByTestId("review-announcer")).toHaveTextContent(/Approval queued\. You can undo it for 5 seconds/);
    expect(screen.getByText(/Approve pending \(undo available\)/)).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4900);
    });
    expect(posts()).toHaveLength(0);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200);
    });
    expect(posts()).toHaveLength(1);
    const req = posts()[0]![0] as Request;
    expect(new URL(req.url).pathname).toBe("/api/mentions/run-1/review");
    const body = await req.clone().json();
    expect(body).toEqual({ action: "approve", reason: "Quote matches the text" });
    expect("reviewer" in body).toBe(false);
  });

  it("undo within the window means nothing is ever sent", async () => {
    const user = await setup();
    await user.type(screen.getByLabelText(/Reason/), "Wrong");
    await user.click(screen.getByRole("radio", { name: "Reject" }));
    await user.click(screen.getByRole("button", { name: "Reject" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    await user.click(screen.getByRole("button", { name: /Undo reject for/ }));
    expect(screen.getByTestId("review-announcer")).toHaveTextContent("Undone. Nothing was sent.");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20_000);
    });
    expect(posts()).toHaveLength(0);
    expect(window.localStorage.getItem("review-outbox:v1")).toBe("[]");
  });

  it("a correction sends corrected_value", async () => {
    const user = await setup();
    await user.click(screen.getByRole("radio", { name: "Correct" }));
    await user.selectOptions(screen.getByLabelText("Correct value"), "position");
    await user.click(screen.getByRole("button", { name: "Correct" }));
    expect(screen.getByText(/Enter a whole number/)).toBeInTheDocument();
    await user.type(screen.getByLabelText(/Position/), "3");
    await user.type(screen.getByLabelText(/Reason/), "It is third");
    await user.click(screen.getByRole("button", { name: "Correct" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5100);
    });
    const body = await (posts()[0]![0] as Request).clone().json();
    expect(body).toEqual({ action: "correct", corrected_value: "position=3", reason: "It is third" });
  });

  it("a refused review stays visible with the server's message", async () => {
    const user = await setup();
    fetchMock.mockImplementation(async (input) => {
      const req = input as Request;
      if (req.method === "POST") return json(409, { detail: "this run has no extracted mention to review" });
      return json(200, runDetail());
    });
    await user.type(screen.getByLabelText(/Reason/), "ok");
    await user.click(screen.getByRole("button", { name: "Approve" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5100);
    });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/this run has no extracted mention to review/);
    expect(within(alert).queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
    expect(within(alert).getByRole("button", { name: "Dismiss" })).toBeInTheDocument();
  });

  it("a queued review survives a reload: the new page resumes it and sends once", async () => {
    const user = await setup();
    await user.type(screen.getByLabelText(/Reason/), "Looks right");
    await user.click(screen.getByRole("button", { name: "Approve" }));
    expect(JSON.parse(window.localStorage.getItem("review-outbox:v1")!)).toHaveLength(1);
    cleanupAll();
    // Reload: a fresh app over the same storage.
    renderWithApp(<RunViewer auditId="aud-1" runId="run-1" />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5100);
    });
    await waitFor(() => expect(posts()).toHaveLength(1));
  });
});

function cleanupAll() {
  // Unmount the current tree like a page unload would (stops timers).
  cleanup();
}
