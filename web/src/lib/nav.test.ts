import { describe, expect, it } from "vitest";
import { NAV_ITEMS, isActive, navItems } from "./nav";

describe("isActive", () => {
  it("treats / and /audits/* as Audits", () => {
    expect(isActive("/", "/")).toBe(true);
    expect(isActive("/audits/new", "/")).toBe(true);
    expect(isActive("/audits/abc/runs/1", "/")).toBe(true);
    expect(isActive("/sources", "/")).toBe(false);
  });
  it("matches other sections by prefix on a segment boundary", () => {
    expect(isActive("/review", "/review")).toBe(true);
    expect(isActive("/review/x", "/review")).toBe(true);
    expect(isActive("/reviewer", "/review")).toBe(false);
  });
});

describe("the Evals page is for the team, not for end users", () => {
  it("is left out of the menu by default", () => {
    expect(NAV_ITEMS.map((i) => i.href)).toEqual(["/", "/sources", "/review"]);
  });
  it("appears only when the flag is set to 1", () => {
    expect(navItems({}).some((i) => i.href === "/evals")).toBe(false);
    expect(navItems({ NEXT_PUBLIC_SHOW_EVALS: "true" }).some((i) => i.href === "/evals")).toBe(false);
    expect(navItems({ NEXT_PUBLIC_SHOW_EVALS: "1" }).map((i) => i.href)).toEqual(["/", "/sources", "/review", "/evals"]);
  });
});
