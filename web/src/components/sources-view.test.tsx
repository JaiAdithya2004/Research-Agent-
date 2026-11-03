import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { nav, resetNav } from "@/test/nav";
import { auditItem, json, renderWithClient } from "@/test/utils";
import { SourcesView } from "./sources-view";

vi.mock("next/navigation", async () => (await import("@/test/nav")).navigationMock);

const fetchMock = vi.fn<typeof fetch>();

function source(over: Record<string, unknown> = {}) {
  return {
    id: "s1",
    url: "https://review.com/acme",
    domain: "review.com",
    final_url: null,
    source_type: "review",
    fetch_status: "ok",
    fetch_reason: "ok",
    brand_on_page: true,
    classification: "review",
    quote: "Acme CRM",
    snapshot_id: "snap-1",
    snapshot_fetched_at: "2026-03-01T10:00:00Z",
    ...over,
  };
}
const detail = (over: Record<string, unknown> = {}) => ({ ...source(), snapshot_text: "Welcome. We reviewed Acme CRM today.", content_hash: "abcdef0123456789", ...over });
const gap = (domain: string, frequency: number, over: Record<string, unknown> = {}) => ({ domain, frequency, competitors: ["Rival"], source_ids: ["s1"], ...over });
const audit = (over: Record<string, unknown> = {}) => ({
  id: "aud-1",
  status: "complete",
  brand: "Acme",
  created_at: "2026-03-01T10:00:00Z",
  completed_at: "2026-03-01T10:05:00Z",
  degraded: false,
  caps_hit: [],
  request: { brand: "Acme", aliases: [], category: "crm", competitors: ["Rival"], questions: ["Q"], repeats: 1, search_mode: "grounded", research_depth: "cited_pages" },
  summary: null,
  ...over,
});

interface Fixtures {
  audit?: unknown;
  gaps?: unknown;
  sources?: unknown;
  source?: unknown;
  gapsStatus?: number;
  sourcesStatus?: number;
  sourceStatus?: number;
}
function route(f: Fixtures = {}) {
  fetchMock.mockImplementation(async (input) => {
    const url = new URL((input as Request).url);
    const path = url.pathname;
    if (path === "/api/audits/aud-1/gaps") return json(f.gapsStatus ?? 200, f.gaps ?? { audit_id: "aud-1", entries: [] });
    if (path === "/api/audits/aud-1/sources") return json(f.sourcesStatus ?? 200, f.sources ?? []);
    if (path.startsWith("/api/audits/aud-1/sources/")) return json(f.sourceStatus ?? 200, f.source ?? detail());
    if (path === "/api/audits/aud-1") return json(200, f.audit ?? audit());
    if (path === "/api/audits") return json(200, { items: [auditItem({ id: "aud-1", brand: "Acme" })], next_cursor: null });
    return json(404, { detail: "nope" });
  });
}
const sourceCalls = () =>
  fetchMock.mock.calls.map(([r]) => new URL((r as Request).url)).filter((u) => u.pathname === "/api/audits/aud-1/sources");

beforeEach(() => {
  resetNav();
  nav.pathname = "/sources";
  nav.search = "audit_id=aud-1";
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.useRealTimers());

describe("SourcesView: audit picker", () => {
  it("asks which audit when none is chosen", async () => {
    nav.search = "";
    route();
    renderWithClient(<SourcesView />);
    const link = await screen.findByRole("link", { name: /Acme/ });
    expect(link).toHaveAttribute("href", "/sources?audit_id=aud-1");
  });
});

describe("SourcesView: gap report", () => {
  it("ranks domains by frequency with counts, competitors and a link to the evidence", async () => {
    route({ gaps: { audit_id: "aud-1", entries: [gap("low.com", 2), gap("top.com", 9, { source_ids: ["s7", "s8"] })] } });
    renderWithClient(<SourcesView />);
    const table = await screen.findByRole("table", { name: /cited for competitors but never for the brand/i });
    const rows = within(table).getAllByRole("row");
    expect(within(rows[1]!).getByText("top.com")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("9")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("Rival")).toBeInTheDocument();
    expect(within(rows[1]!).getByRole("link", { name: /Open the evidence/ })).toHaveAttribute("href", "/sources?audit_id=aud-1&source=s7");
    expect(within(rows[1]!).getByText("2 pages checked from this domain")).toBeInTheDocument();
    expect(within(rows[2]!).getByText("low.com")).toBeInTheDocument();
  });

  it("says when no page was fetched for a gap domain", async () => {
    route({ gaps: { audit_id: "aud-1", entries: [gap("never.com", 3, { source_ids: [] })] } });
    renderWithClient(<SourcesView />);
    expect(await screen.findByText("No page was fetched for this domain")).toBeInTheDocument();
  });

  it("caps long reports at 10 domains with an explicit count and a way to see all", async () => {
    const entries = Array.from({ length: 14 }, (_, i) => gap(`d${i}.com`, 50 - i));
    route({ gaps: { audit_id: "aud-1", entries } });
    renderWithClient(<SourcesView />);
    expect(await screen.findByText("Showing the top 10 of 14 domains.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show all 14" }));
    expect(screen.getByText("14 domains in total.")).toBeInTheDocument();
  });

  it("empty, for a finished audit and for a running one", async () => {
    route();
    const first = renderWithClient(<SourcesView />);
    expect(await screen.findByRole("heading", { name: "No source gaps found" })).toBeInTheDocument();
    first.unmount();
    route({ audit: audit({ status: "running" }) });
    renderWithClient(<SourcesView />);
    expect(await screen.findByText(/audit is still running/)).toBeInTheDocument();
  });

  it("error with retry", async () => {
    route({ gapsStatus: 500, gaps: { detail: "gap boom" } });
    renderWithClient(<SourcesView />);
    expect(await screen.findByText("Could not load the gap report", {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getByText("gap boom")).toBeInTheDocument();
  });
});

describe("SourcesView: source table", () => {
  const mixed = [
    source({ id: "s1", domain: "found.com", brand_on_page: true }),
    source({ id: "s2", domain: "absent.com", brand_on_page: false }),
    source({ id: "s3", domain: "down.com", fetch_status: "unreachable", fetch_reason: "timeout", brand_on_page: null, snapshot_id: null, snapshot_fetched_at: null, quote: null }),
    source({ id: "s4", domain: "wall.com", fetch_status: "paywalled", fetch_reason: "paywall_marker", brand_on_page: null }),
  ];

  it("shows type, fetch status, brand-on-page and snapshot time, with unreachable clearly not 'brand not found'", async () => {
    route({ sources: mixed });
    renderWithClient(<SourcesView />);
    const rows = await screen.findAllByTestId("source-row");
    expect(rows).toHaveLength(4);
    expect(within(rows[0]!).getByText("Brand found on page")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("Page read: brand not on it")).toBeInTheDocument();
    expect(within(rows[2]!).getByText("Could not read: unreachable")).toBeInTheDocument();
    expect(within(rows[2]!).queryByText(/brand not on it/)).not.toBeInTheDocument();
    expect(within(rows[2]!).getByText("No snapshot")).toBeInTheDocument();
    expect(within(rows[3]!).getByText("Could not read: paywalled")).toBeInTheDocument();
    expect(screen.getByText("2 pages could not be read")).toBeInTheDocument();
    expect(screen.getByTestId("source-count")).toHaveTextContent("1 with the brand, 1 read without it, 2 could not be read");
  });

  it("filters by type and by fetch status through the API and the URL", async () => {
    route({ sources: mixed });
    renderWithClient(<SourcesView />);
    await screen.findAllByTestId("source-row");
    await userEvent.selectOptions(screen.getByLabelText("Type"), "forum");
    expect(nav.replace).toHaveBeenLastCalledWith("/sources?audit_id=aud-1&source_type=forum", { scroll: false });
    await userEvent.selectOptions(screen.getByLabelText("Fetch status"), "unreachable");
    expect(nav.replace).toHaveBeenLastCalledWith("/sources?audit_id=aud-1&fetch_status=unreachable", { scroll: false });
  });

  it("sends the filters from the URL to the API", async () => {
    nav.search = "audit_id=aud-1&source_type=news&fetch_status=blocked&domain=x.com";
    route({ sources: [] });
    renderWithClient(<SourcesView />);
    await screen.findByRole("heading", { name: "No sources match these filters" });
    const q = sourceCalls()[0]!.searchParams;
    expect(q.get("source_type")).toBe("news");
    expect(q.get("fetch_status")).toBe("blocked");
    expect(q.get("domain")).toBe("x.com");
  });

  it("applies the domain filter on submit", async () => {
    route({ sources: mixed });
    renderWithClient(<SourcesView />);
    await screen.findAllByTestId("source-row");
    await userEvent.type(screen.getByLabelText(/Domain/), "down.com{Enter}");
    expect(nav.replace).toHaveBeenLastCalledWith("/sources?audit_id=aud-1&domain=down.com", { scroll: false });
  });

  it("empty without filters explains that no pages were researched", async () => {
    route({ sources: [] });
    renderWithClient(<SourcesView />);
    expect(await screen.findByRole("heading", { name: "No pages were researched" })).toBeInTheDocument();
  });

  it("error shows the server message", async () => {
    route({ sourcesStatus: 500, sources: { detail: "sources boom" } });
    renderWithClient(<SourcesView />);
    expect(await screen.findByText("Could not load the sources", {}, { timeout: 4000 })).toBeInTheDocument();
  });

  it("caps the table at 100 rows and says how many are hidden", async () => {
    const many = Array.from({ length: 130 }, (_, i) => source({ id: `s${i}`, domain: `d${i}.com` }));
    route({ sources: many });
    renderWithClient(<SourcesView />);
    expect(await screen.findAllByTestId("source-row")).toHaveLength(100);
    expect(screen.getByTestId("source-count")).toHaveTextContent("Showing the first 100 of 130 sources.");
    await userEvent.click(screen.getByRole("button", { name: "Show all 130" }));
    expect(screen.getAllByTestId("source-row")).toHaveLength(130);
  });

  it("shows the capped and degraded notices of the audit", async () => {
    route({
      sources: mixed,
      audit: audit({ status: "capped", caps_hit: ["fetch_cap"], degraded: true }),
    });
    renderWithClient(<SourcesView />);
    expect(await screen.findByTestId("banner-capped")).toHaveTextContent(/Page fetch cap/);
    expect(screen.getByTestId("banner-degraded")).toBeInTheDocument();
    expect(await screen.findByText(/stopped fetching at its page limit/)).toBeInTheDocument();
  });

  it("a row opens the source through the URL", async () => {
    route({ sources: mixed });
    renderWithClient(<SourcesView />);
    const rows = await screen.findAllByTestId("source-row");
    expect(within(rows[0]!).getByRole("link", { name: /found.com/ })).toHaveAttribute("href", "/sources?audit_id=aud-1&source=s1");
  });
});

describe("SourcesView: source detail", () => {
  beforeEach(() => {
    nav.search = "audit_id=aud-1&source=s1";
  });

  it("shows the snapshot with the brand quote highlighted, and when it was taken", async () => {
    route({ sources: [source()] });
    renderWithClient(<SourcesView />);
    const region = await screen.findByRole("region", { name: "Page snapshot text" });
    expect(within(region).getByTestId("snapshot-highlight")).toHaveTextContent("Acme CRM");
    expect(region.textContent).toContain("Welcome. We reviewed ");
    const time = screen.getByTestId("snapshot-time");
    expect(time).toHaveTextContent(/Snapshot taken/);
    expect(time.querySelector("time")).toHaveAttribute("datetime", "2026-03-01T10:00:00Z");
    expect(time).toHaveTextContent(/days ago/);
    expect(time).toHaveTextContent(/live page may have changed/); // months old: stale
  });

  it("renders snapshot text as plain text, never HTML", async () => {
    route({ source: detail({ snapshot_text: '<script>window.__pwned = 1</script><img src=x onerror="alert(1)"> Acme CRM', quote: "Acme CRM" }), sources: [source()] });
    const { container } = renderWithClient(<SourcesView />);
    const region = await screen.findByRole("region", { name: "Page snapshot text" });
    expect(region.textContent).toContain("<script>window.__pwned = 1</script>");
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
  });

  it("an unreadable page has no snapshot and says so, not 'brand not found'", async () => {
    route({
      source: detail({ fetch_status: "unreachable", fetch_reason: "http_not_found", brand_on_page: null, snapshot_text: null, snapshot_fetched_at: null, quote: null, content_hash: null }),
      sources: [source()],
    });
    renderWithClient(<SourcesView />);
    expect(await screen.findByText("This page could not be read, so there is no snapshot to show")).toBeInTheDocument();
    expect(screen.queryByText(/brand not on it/)).not.toBeInTheDocument();
    expect(screen.getByText("The page was not found (HTTP 404 or 410)")).toBeInTheDocument();
    expect(screen.getByText(/No snapshot time was recorded/)).toBeInTheDocument();
  });

  it("a page read without the brand shows the text with no highlight and says so", async () => {
    route({ source: detail({ brand_on_page: false, quote: null }), sources: [source()] });
    renderWithClient(<SourcesView />);
    await screen.findByRole("region", { name: "Page snapshot text" });
    expect(screen.queryByTestId("snapshot-highlight")).not.toBeInTheDocument();
    expect(screen.getByText(/does not appear in it, so nothing is highlighted/)).toBeInTheDocument();
  });

  it("explicitly truncates a very long snapshot and can show the rest", async () => {
    route({ source: detail({ snapshot_text: "x".repeat(60_000), quote: null, brand_on_page: false }), sources: [source()] });
    renderWithClient(<SourcesView />);
    expect(await screen.findByTestId("snapshot-truncated")).toHaveTextContent("Showing the first 50,000 of 60,000 characters.");
    await userEvent.click(screen.getByRole("button", { name: "Show the rest" }));
    expect(screen.getByText("60,000 characters, all shown.")).toBeInTheDocument();
  });

  it("mentions a redirect and attributes the evidence to the final domain", async () => {
    route({ source: detail({ final_url: "https://other.com/acme" }), sources: [source()] });
    renderWithClient(<SourcesView />);
    expect(await screen.findByText(/Redirected to https:\/\/other.com\/acme/)).toBeInTheDocument();
  });

  it("not found and error states, and close", async () => {
    route({ sourceStatus: 404, source: { detail: "source not found" }, sources: [source()] });
    renderWithClient(<SourcesView />);
    expect(await screen.findByText("Source not found")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(nav.replace).toHaveBeenLastCalledWith("/sources?audit_id=aud-1", { scroll: false });
  });

  it("loading", async () => {
    route({ sources: [source()] });
    renderWithClient(<SourcesView />);
    expect(screen.getByRole("status", { name: "Loading source" })).toBeInTheDocument();
    await screen.findByRole("region", { name: "Page snapshot text" });
  });
});
