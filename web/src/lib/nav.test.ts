import { describe, expect, it } from "vitest";
import { isActive } from "./nav";

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
