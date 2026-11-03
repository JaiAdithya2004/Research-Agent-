import AxeBuilder from "@axe-core/playwright";
import fs from "node:fs";
import path from "node:path";
import type { Page } from "@playwright/test";
import { expect, fakeApi, test } from "./helpers";
import { FOUND_SOURCE, IDS, UNREADABLE_SOURCE } from "./fixtures";

/** Phone width. Screenshots land in e2e/screenshots/ (gitignored) so they can be looked at. */
test.use({ viewport: { width: 375, height: 812 }, hasTouch: true, isMobile: true });

const SHOTS = path.join(import.meta.dirname, "screenshots");
const MIN_TARGET = 24; // WCAG 2.2 SC 2.5.8 (AA) minimum; 44 is the comfortable size we aim for and report on

const PAGES: { name: string; path: string; ready: (p: Page) => Promise<void> }[] = [
  { name: "audits", path: "/", ready: async (p) => { await expect(p.getByRole("table", { name: /Audits, newest first/ })).toBeVisible(); } },
  { name: "new-audit", path: "/audits/new", ready: async (p) => { await expect(p.getByLabel("Question 1", { exact: true })).toBeVisible(); } },
  { name: "audit-running", path: `/audits/${IDS.running}`, ready: async (p) => { await expect(p.getByTestId("connection-state")).toContainText("Live"); await fakeApi.emit(IDS.running, 7); await expect(p.getByTestId("progress-text")).toHaveText(/3 of 6/); } },
  { name: "audit-complete", path: `/audits/${IDS.complete}`, ready: async (p) => { await expect(p.getByTestId("metric-mention-rate")).toBeVisible(); } },
  { name: "run", path: `/audits/${IDS.complete}/runs/${IDS.reviewRun}`, ready: async (p) => { await expect(p.locator("mark").first()).toBeVisible(); } },
  { name: "review", path: "/review", ready: async (p) => { await expect(p.getByText("2 flagged mentions loaded")).toBeVisible(); } },
  { name: "sources-picker", path: "/sources", ready: async (p) => { await expect(p.getByRole("heading", { name: "Choose an audit" })).toBeVisible(); } },
  { name: "sources", path: `/sources?audit_id=${IDS.complete}`, ready: async (p) => { await expect(p.getByTestId("source-row")).toHaveCount(3); } },
  { name: "source-found", path: `/sources?audit_id=${IDS.complete}&source=${FOUND_SOURCE.id}`, ready: async (p) => { await expect(p.locator("mark")).toBeVisible(); } },
  { name: "source-unreadable", path: `/sources?audit_id=${IDS.complete}&source=${UNREADABLE_SOURCE.id}`, ready: async (p) => { await expect(p.getByText(/could not be read, so there is no snapshot/)).toBeVisible(); } },
  { name: "evals", path: "/evals", ready: async (p) => { await expect(p.getByRole("table", { name: /Prompt versions compared/ })).toBeVisible(); } },
];

for (const theme of ["light", "dark"] as const) {
  test.describe(`375px, ${theme}`, () => {
    test.use({ colorScheme: theme });
    for (const c of PAGES) {
      test(`${c.name}: no horizontal page scroll, targets reachable`, async ({ page }, info) => {
        await fakeApi.reset();
        await page.addInitScript((t) => window.localStorage.setItem("theme", t), theme);
        await page.goto(c.path);
        await expect(page.locator("main h1:visible").first()).toBeVisible();
        await c.ready(page);

        fs.mkdirSync(SHOTS, { recursive: true });
        await page.screenshot({ path: path.join(SHOTS, `${c.name}-${theme}.png`), fullPage: true });
        info.annotations.push({ type: "screenshot", description: `${c.name}-${theme}.png` });

        const widths = await page.evaluate(() => ({
          scroll: document.scrollingElement!.scrollWidth,
          client: document.scrollingElement!.clientWidth,
          offenders: [...document.querySelectorAll("body *")]
            .filter((el) => el.getBoundingClientRect().right > document.documentElement.clientWidth + 1 && !el.closest('[role="region"][tabindex="0"]'))
            .sort((a, b) => b.getBoundingClientRect().right - a.getBoundingClientRect().right)
            .slice(0, 5)
            .map((el) => `${el.tagName.toLowerCase()}.${String(el.className).slice(0, 60)} right=${Math.round(el.getBoundingClientRect().right)}`),
        }));
        expect(widths.scroll, `page scrolls sideways; offenders: ${widths.offenders.join(", ")}`).toBeLessThanOrEqual(widths.client);

        const small = await page.evaluate((min) => {
          const out: { el: string; w: number; h: number }[] = [];
          const sel = 'button, select, textarea, input:not([type="radio"]):not([type="checkbox"]), label:has(input[type="radio"]), nav a, [role="tab"], summary';
          for (const el of document.querySelectorAll(sel)) {
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0) continue;
            if (el.closest(".sr-only") || getComputedStyle(el).position === "absolute" && r.width <= 1) continue;
            if (r.width < min || r.height < min) out.push({ el: `${el.tagName.toLowerCase()} ${(el.getAttribute("aria-label") ?? el.textContent ?? "").trim().slice(0, 30)}`, w: Math.round(r.width), h: Math.round(r.height) });
          }
          return out;
        }, MIN_TARGET);
        expect(small, `tap targets under ${MIN_TARGET}px: ${JSON.stringify(small)}`).toEqual([]);

      });
    }
  });
}

test.describe("375px navigation menu", () => {
  test("opens and closes by keyboard and by touch, focus returns to the trigger", async ({ page }) => {
    await fakeApi.reset();
    await page.goto("/");
    const trigger = page.getByRole("button", { name: "Open menu" });
    await expect(trigger).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Main" })).toHaveCount(0); // the sidebar is hidden on a phone

    // Keyboard: focus the trigger, Enter opens, Tab stays inside, Escape closes.
    await trigger.focus();
    await page.keyboard.press("Enter");
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    const nav = dialog.getByRole("navigation", { name: "Main" });
    await expect(nav.getByRole("link")).toHaveCount(4);
    for (let i = 0; i < 8; i++) {
      await page.keyboard.press("Tab");
      expect(await page.evaluate(() => !!document.activeElement?.closest('[role="dialog"]'))).toBe(true);
    }
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(trigger).toBeFocused();

    // Touch: open, pick a page, the menu closes and the page changes.
    await trigger.tap();
    await expect(dialog).toBeVisible();
    await dialog.getByRole("link", { name: "Review" }).tap();
    await expect(page).toHaveURL(/\/review$/);
    await expect(dialog).toHaveCount(0);

    fs.mkdirSync(SHOTS, { recursive: true });
    await trigger.tap();
    await expect(dialog).toBeVisible();
    await page.waitForTimeout(700); // let the slide-in finish before the screenshot and the contrast check
    await page.screenshot({ path: path.join(SHOTS, "menu-open.png") });
    const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
    expect(axe.violations.map((v) => v.id)).toEqual([]);
  });
});
