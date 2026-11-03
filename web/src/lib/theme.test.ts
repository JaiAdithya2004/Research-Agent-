import { describe, expect, it } from "vitest";
import { THEME_INIT_SCRIPT, parsePreference, resolveTheme } from "./theme";

describe("theme", () => {
  it("falls back to system for unknown stored values", () => {
    expect(parsePreference("dark")).toBe("dark");
    expect(parsePreference("purple")).toBe("system");
    expect(parsePreference(null)).toBe("system");
  });
  it("resolves system from the media query", () => {
    expect(resolveTheme("system", true)).toBe("dark");
    expect(resolveTheme("system", false)).toBe("light");
    expect(resolveTheme("light", true)).toBe("light");
  });
  it("the init script is valid JavaScript and sets the class synchronously", () => {
    expect(() => new Function(THEME_INIT_SCRIPT)).not.toThrow();
    document.documentElement.className = "";
    localStorage.setItem("theme", "dark");
    new Function(THEME_INIT_SCRIPT)();
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    localStorage.clear();
  });
});
