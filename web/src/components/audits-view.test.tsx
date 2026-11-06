import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { nav, resetNav } from "@/test/nav";
import { auditItem, json, renderWithClient } from "@/test/utils";
import { AuditsView } from "./audits-view";

vi.mock("next/navigation", async () => (await import("@/test/nav")).navigationMock);

const fetchMock = vi.fn<typeof fetch>();
function requestUrl(call: number): URL {
  return new URL((fetchMock.mock.calls[call]![0] as Request).url);
}

beforeEach(() => {
  resetNav();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

describe("AuditsView", () => {
  it("says in one short paragraph what the tool is for", async () => {
    fetchMock.mockResolvedValue(json(200, { items: [], next_cursor: null }));
    renderWithClient(<AuditsView />);
    const intro = screen.getByRole("region", { name: "About this tool" });
    expect(intro).toHaveTextContent(/asks them the questions your customers ask/i);
    expect(intro).toHaveTextContent(/recommend your brand/i);
  });

  it("shows a loading skeleton, then the table with distinct statuses", async () => {
    fetchMock.mockResolvedValue(
      json(200, {
        items: [
          auditItem({ id: "1", brand: "Alpha", status: "running", mention_rate: null }),
          auditItem({ id: "2", brand: "Beta", status: "complete", mention_rate: 0.5 }),
          auditItem({ id: "3", brand: "Gamma", status: "capped", caps_hit: ["cost_cap"], flagged_count: 2 }),
          auditItem({ id: "4", brand: "Delta", status: "failed", degraded: true }),
        ],
        next_cursor: null,
      }),
    );
    renderWithClient(<AuditsView />);
    expect(screen.getByRole("status", { name: "Loading audits" })).toBeInTheDocument();

    const table = await screen.findByRole("table");
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(5);
    const [, alpha, beta, gamma, delta] = rows;
    expect(within(alpha!).getByText("Running")).toBeInTheDocument();
    expect(within(alpha!).getByText("Not available")).toBeInTheDocument(); // null mention rate is not "0%"
    expect(within(alpha!).queryByText("0%")).not.toBeInTheDocument();
    expect(within(beta!).getByText("Complete")).toBeInTheDocument();
    expect(within(beta!).getByText("50%")).toBeInTheDocument();
    expect(within(gamma!).getByText("Capped")).toBeInTheDocument();
    expect(within(gamma!).getByText("Cost cap hit")).toBeInTheDocument();
    // Developer numbers are not shown in the list: no cost, no flagged count.
    const headers = within(table).getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers).toEqual(["Brand", "Status", "AI visibility", "Created", "Actions"]);
    expect(within(gamma!).queryByText(/flagged/)).not.toBeInTheDocument();
    expect(within(beta!).getByRole("button", { name: "Delete audit for Beta" })).toBeEnabled();
    expect(within(alpha!).getByRole("button", { name: "Delete audit for Alpha" })).toBeDisabled(); // still running
    expect(within(delta!).getByText("Failed")).toBeInTheDocument();
    expect(within(delta!).getByText("Degraded")).toBeInTheDocument();
    expect(within(beta!).getByRole("link", { name: "Beta" })).toHaveAttribute("href", "/audits/2");
    expect(screen.getByText("That is every audit.")).toBeInTheDocument();
  });

  it("shows the empty state with a call to action when there are no audits at all", async () => {
    fetchMock.mockResolvedValue(json(200, { items: [], next_cursor: null }));
    renderWithClient(<AuditsView />);
    expect(await screen.findByRole("heading", { name: "No audits yet" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Create your first audit" })).toHaveAttribute("href", "/audits/new");
  });

  it("shows a distinct no-match state with a clear-filters action", async () => {
    nav.search = "status=failed&brand=zzz";
    fetchMock.mockResolvedValue(json(200, { items: [], next_cursor: null }));
    renderWithClient(<AuditsView />);
    expect(await screen.findByRole("heading", { name: "No audits match these filters" })).toBeInTheDocument();
    expect(requestUrl(0).searchParams.get("status")).toBe("failed");
    expect(requestUrl(0).searchParams.get("brand")).toBe("zzz");
    const user = userEvent.setup();
    const buttons = screen.getAllByRole("button", { name: "Clear filters" });
    await user.click(buttons[buttons.length - 1]!);
    expect(nav.replace).toHaveBeenCalledWith("/", { scroll: false });
  });

  it("shows an error with a working retry", async () => {
    fetchMock.mockResolvedValueOnce(json(503, { detail: "database is down" }));
    fetchMock.mockResolvedValueOnce(json(200, { items: [auditItem()], next_cursor: null }));
    renderWithClient(<AuditsView />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("database is down");
    await userEvent.setup().click(within(alert).getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("table")).toBeInTheDocument();
  });

  it("paginates with a keyboard-reachable Load more button using the cursor", async () => {
    fetchMock.mockResolvedValueOnce(json(200, { items: [auditItem({ id: "1", brand: "First" })], next_cursor: "CUR1" }));
    fetchMock.mockResolvedValueOnce(json(200, { items: [auditItem({ id: "2", brand: "Second" })], next_cursor: null }));
    renderWithClient(<AuditsView />);
    await screen.findByText("First");
    const more = screen.getByRole("button", { name: "Load more audits" });
    more.focus();
    await userEvent.setup().keyboard("{Enter}");
    await screen.findByText("Second");
    expect(requestUrl(1).searchParams.get("cursor")).toBe("CUR1");
    expect(screen.getByText("First")).toBeInTheDocument(); // earlier rows stay
    expect(screen.queryByRole("button", { name: "Load more audits" })).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("list-live-region")).toHaveTextContent("2 audits shown"));
  });

  it("keeps loaded rows and offers a retry when loading more fails", async () => {
    fetchMock.mockResolvedValueOnce(json(200, { items: [auditItem({ id: "1", brand: "First" })], next_cursor: "CUR1" }));
    fetchMock.mockResolvedValueOnce(json(500, { detail: "boom" }));
    renderWithClient(<AuditsView />);
    await screen.findByText("First");
    await userEvent.setup().click(screen.getByRole("button", { name: "Load more audits" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    expect(screen.getByText("First")).toBeInTheDocument();
  });

  it("writes the status filter and the debounced brand filter to the URL", async () => {
    fetchMock.mockResolvedValue(json(200, { items: [auditItem()], next_cursor: null }));
    renderWithClient(<AuditsView />);
    await screen.findByRole("table");
    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText("Status"), "capped");
    expect(nav.replace).toHaveBeenLastCalledWith("/?status=capped", { scroll: false });

    nav.replace.mockClear();
    await user.type(screen.getByLabelText("Brand"), "Acm");
    expect(nav.replace).not.toHaveBeenCalled(); // debounced: nothing yet
    await waitFor(() => expect(nav.replace).toHaveBeenCalledTimes(1));
    expect(nav.replace).toHaveBeenCalledWith("/?brand=Acm", { scroll: false });
  });

  it("restores filters from the URL on load", async () => {
    nav.search = "status=running&brand=Acme";
    fetchMock.mockResolvedValue(json(200, { items: [auditItem()], next_cursor: null }));
    renderWithClient(<AuditsView />);
    await screen.findByRole("table");
    expect(screen.getByLabelText("Status")).toHaveValue("running");
    expect(screen.getByLabelText("Brand")).toHaveValue("Acme");
  });
});
