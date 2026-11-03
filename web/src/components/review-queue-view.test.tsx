import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { json, renderWithApp } from "@/test/utils";
import { ReviewQueueView } from "./review-queue-view";

const fetchMock = vi.fn<typeof fetch>();

function item(over: Record<string, unknown> = {}) {
  return {
    run_id: "run-1",
    audit_id: "aud-1",
    brand: "Acme",
    question: "Best CRM?",
    provider: "gemini",
    model: "gem-1",
    search_mode: "grounded",
    repeat_idx: 0,
    verdict_reason: "Quote not found in the answer.",
    quote: "Acme CRM",
    brand_mentioned: true,
    position: 1,
    raw_answer: "Try Acme CRM today.",
    highlights: [{ start: 4, end: 12 }],
    created_at: "2026-03-01T10:00:00Z",
    ...over,
  };
}
const queue = (items: unknown[], next: string | null = null) => json(200, { items, next_cursor: next });
const posts = () => fetchMock.mock.calls.filter(([r]) => (r as Request).method === "POST");
const gets = () => fetchMock.mock.calls.filter(([r]) => (r as Request).method === "GET");

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

describe("ReviewQueueView states", () => {
  it("loading, then flagged items with the quote highlighted and a reason for the flag", async () => {
    fetchMock.mockResolvedValue(queue([item(), item({ run_id: "run-2", question: "Cheap CRM?" })]));
    renderWithApp(<ReviewQueueView auditId={null} />);
    expect(screen.getByRole("status", { name: "Loading the review queue" })).toBeInTheDocument();
    const cards = await screen.findAllByRole("article");
    expect(cards).toHaveLength(2);
    expect(within(cards[0]!).getByTestId("quote-highlight")).toHaveTextContent("Acme CRM");
    expect(within(cards[0]!).getByText("Quote not found in the answer.")).toBeInTheDocument();
    expect(within(cards[0]!).getByRole("link", { name: /full answer/ })).toHaveAttribute("href", "/audits/aud-1/runs/run-1");
    expect(screen.getByText("That is every flagged mention.")).toBeInTheDocument();
  });

  it("passes ?audit_id= to the API and says the view is filtered", async () => {
    fetchMock.mockResolvedValue(queue([item()]));
    renderWithApp(<ReviewQueueView auditId="aud-9" />);
    await screen.findAllByRole("article");
    expect(new URL((fetchMock.mock.calls[0]![0] as Request).url).searchParams.get("audit_id")).toBe("aud-9");
    expect(screen.getByText(/for one audit/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Show every audit" })).toHaveAttribute("href", "/review");
  });

  it("empty", async () => {
    fetchMock.mockResolvedValue(queue([]));
    renderWithApp(<ReviewQueueView auditId={null} />);
    expect(await screen.findByRole("heading", { name: "Nothing waiting for review" })).toBeInTheDocument();
  });

  it("error with retry", async () => {
    fetchMock.mockResolvedValueOnce(json(500, { detail: "db down" }));
    renderWithApp(<ReviewQueueView auditId={null} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("db down");
    fetchMock.mockResolvedValue(queue([item()]));
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findAllByRole("article")).toHaveLength(1);
  });

  it("capped: says more are waiting and loads the next page by cursor", async () => {
    fetchMock.mockResolvedValueOnce(queue([item()], "cur-2"));
    renderWithApp(<ReviewQueueView auditId={null} />);
    expect(await screen.findByText(/Showing the first 1\. More flagged mentions are waiting\./)).toBeInTheDocument();
    fetchMock.mockResolvedValueOnce(queue([item({ run_id: "run-2", question: "Second?" })]));
    await userEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findAllByRole("article")).toHaveLength(2);
    expect(new URL((fetchMock.mock.calls[1]![0] as Request).url).searchParams.get("cursor")).toBe("cur-2");
  });

  it("partial: a failed 'load more' keeps the loaded items on screen", async () => {
    fetchMock.mockResolvedValueOnce(queue([item()], "cur-2"));
    renderWithApp(<ReviewQueueView auditId={null} />);
    await screen.findByRole("button", { name: "Load more" });
    fetchMock.mockResolvedValueOnce(json(500, { detail: "slow" }));
    await userEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findByText("Could not load more")).toBeInTheDocument();
    expect(screen.getAllByRole("article")).toHaveLength(1);
  });

  it("an item without a raw answer says so instead of showing an empty box", async () => {
    fetchMock.mockResolvedValue(queue([item({ raw_answer: null, highlights: [], quote: null })]));
    renderWithApp(<ReviewQueueView auditId={null} />);
    expect(await screen.findByText("The raw answer is not available for this run.")).toBeInTheDocument();
  });
});

describe("ReviewQueueView optimistic review with undo", () => {
  async function setup() {
    fetchMock.mockImplementation(async (input) => {
      const req = input as Request;
      if (req.method === "POST") return json(201, { id: "rev" });
      return queue([item(), item({ run_id: "run-2", question: "Cheap CRM?" })]);
    });
    renderWithApp(<ReviewQueueView auditId={null} />);
    const cards = await screen.findAllByRole("article");
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"] });
    vi.stubGlobal("jest", { advanceTimersByTime: (ms: number) => vi.advanceTimersByTime(ms) });
    const user = userEvent.setup({ advanceTimers: (ms) => vi.advanceTimersByTime(ms) });
    return { user, first: cards[0]! };
  }

  it("removes the item at once, restores it on undo, and never POSTs", async () => {
    const { user, first } = await setup();
    await user.type(within(first).getByLabelText(/Reason/), "Not what the text says");
    await user.click(within(first).getByRole("radio", { name: "Reject" }));
    await user.click(within(first).getByRole("button", { name: "Reject" }));

    expect(screen.getAllByRole("article")).toHaveLength(1);
    expect(screen.getByText(/1 hidden while being saved/)).toBeInTheDocument();
    expect(screen.queryByText("Best CRM?", { selector: "h2" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Flagged mentions" })).toHaveFocus();

    await user.click(screen.getByRole("button", { name: /Undo reject for Best CRM/ }));
    expect(screen.getAllByRole("article")).toHaveLength(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(posts()).toHaveLength(0);
  });

  it("sends exactly one POST after 5 seconds and refreshes the queue", async () => {
    const { user, first } = await setup();
    await user.type(within(first).getByLabelText(/Reason/), "Right");
    await user.click(within(first).getByRole("button", { name: "Approve" }));
    const before = gets().length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5100);
    });
    expect(posts()).toHaveLength(1);
    expect(new URL((posts()[0]![0] as Request).url).pathname).toBe("/api/mentions/run-1/review");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(gets().length).toBeGreaterThan(before); // invalidated and refetched
  });

  it("when everything is decided but still saving, the empty state says so", async () => {
    fetchMock.mockImplementation(async (input) => {
      if ((input as Request).method === "POST") return json(201, { id: "rev" });
      return queue([item()]);
    });
    renderWithApp(<ReviewQueueView auditId={null} />);
    const [card] = await screen.findAllByRole("article");
    const user = userEvent.setup();
    await user.type(within(card!).getByLabelText(/Reason/), "ok");
    await user.click(within(card!).getByRole("button", { name: "Approve" }));
    expect(await screen.findByRole("heading", { name: "All caught up for now" })).toBeInTheDocument();
  });
});
