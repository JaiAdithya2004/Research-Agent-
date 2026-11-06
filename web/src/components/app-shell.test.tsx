import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "./app-shell";

let pathname = "/";
vi.mock("next/navigation", () => ({ usePathname: () => pathname }));

beforeEach(() => {
  pathname = "/";
  localStorage.clear();
  document.documentElement.className = "";
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })) as unknown as typeof window.matchMedia;
});

describe("AppShell navigation", () => {
  it("renders the three sections in a labelled nav and marks the current page", () => {
    pathname = "/review";
    render(
      <AppShell>
        <h1>Page</h1>
      </AppShell>,
    );
    const nav = screen.getAllByRole("navigation", { name: "Main" })[0]!;
    const links = within(nav).getAllByRole("link");
    expect(links.map((l) => l.textContent)).toEqual(["Audits", "Sources", "Review"]);
    expect(within(nav).getByRole("link", { name: "Review" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "Audits" })).not.toHaveAttribute("aria-current");
  });

  it("marks Audits current on audit sub-pages", () => {
    pathname = "/audits/abc";
    render(<AppShell>x</AppShell>);
    const nav = screen.getAllByRole("navigation", { name: "Main" })[0]!;
    expect(within(nav).getByRole("link", { name: "Audits" })).toHaveAttribute("aria-current", "page");
  });

  it("has a skip link and a main landmark", () => {
    render(<AppShell>x</AppShell>);
    expect(screen.getByRole("link", { name: "Skip to content" })).toHaveAttribute("href", "#main");
    expect(screen.getByRole("main")).toBeInTheDocument();
  });

  it("opens the mobile menu as a dialog with the same links, and closes on navigate", async () => {
    const user = userEvent.setup();
    render(<AppShell>x</AppShell>);
    await user.click(screen.getByRole("button", { name: "Open menu" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("link", { name: "Review" })).toBeInTheDocument();
    expect(within(dialog).queryByRole("link", { name: "Evals" })).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("link", { name: "Review" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
