import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";
import { expect, fakeApi, test } from "./helpers";
import { appState, emptyState, FOUND_SOURCE, IDS, UNREADABLE_SOURCE, type FakeState } from "./fixtures";

/** WCAG 2.0 and 2.1, levels A and AA. Nothing is disabled; see A11Y.md for what is out of scope. */
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];

type Theme = "light" | "dark";
const THEMES: Theme[] = ["light", "dark"];

interface PageCase {
  name: string;
  path: string;
  state?: () => FakeState;
  /** Waits until the page shows its real content, not a skeleton. */
  ready: (page: Page) => Promise<void>;
}

async function settled(page: Page) {
  await expect(page.locator("main h1:visible").first()).toBeVisible();
  await expect(page.locator('[aria-label^="Loading"]')).toHaveCount(0);
}

const CASES: PageCase[] = [
  { name: "audits list", path: "/", ready: async (p) => { await settled(p); await expect(p.getByRole("table", { name: /Audits, newest first/ })).toBeVisible(); } },
  { name: "audits list, empty", path: "/", state: emptyState, ready: async (p) => { await settled(p); await expect(p.getByText(/No audits yet/i).first()).toBeVisible(); } },
  { name: "new audit", path: "/audits/new", ready: async (p) => { await settled(p); await expect(p.getByLabel("Question 1", { exact: true })).toBeVisible(); } },
  {
    name: "audit detail, running",
    path: `/audits/${IDS.running}`,
    ready: async (p) => {
      await expect(p.getByTestId("connection-state")).toContainText("Live");
      await fakeApi.emit(IDS.running, 7);
      await expect(p.getByTestId("progress-text")).toHaveText(/3 of 6 runs finished/);
    },
  },
  { name: "audit detail, completed", path: `/audits/${IDS.complete}`, ready: async (p) => { await settled(p); await expect(p.getByTestId("metric-mention-rate")).toBeVisible(); await expect(p.getByText("Q one?").or(p.getByText("What is the best CRM for startups?")).first()).toBeVisible(); } },
  { name: "audit detail, capped and degraded with failed runs", path: `/audits/${IDS.capped}`, ready: async (p) => { await settled(p); await expect(p.getByTestId("metric-mention-rate")).toBeVisible(); } },
  { name: "audit detail, failed", path: `/audits/${IDS.failed}`, ready: async (p) => { await settled(p); await expect(p.getByText("Failed", { exact: true }).first()).toBeVisible(); } },
  { name: "run viewer", path: `/audits/${IDS.complete}/runs/${IDS.reviewRun}`, ready: async (p) => { await settled(p); await expect(p.locator("mark").first()).toBeVisible(); } },
  { name: "review queue", path: "/review", ready: async (p) => { await settled(p); await expect(p.getByText("2 flagged mentions loaded")).toBeVisible(); } },
  { name: "review queue, empty", path: "/review", state: emptyState, ready: async (p) => { await settled(p); await expect(p.getByText("Nothing waiting for review")).toBeVisible(); } },
  { name: "sources, audit picker", path: "/sources", ready: async (p) => { await settled(p); await expect(p.getByRole("heading", { name: "Choose an audit" })).toBeVisible(); } },
  { name: "sources with an audit and gap report", path: `/sources?audit_id=${IDS.complete}`, ready: async (p) => { await settled(p); await expect(p.getByTestId("source-row")).toHaveCount(3); await expect(p.getByRole("rowheader", { name: "g2.example" })).toBeVisible(); } },
  { name: "sources, empty audit", path: `/sources?audit_id=${IDS.emptyAudit}`, ready: async (p) => { await settled(p); await expect(p.getByText("No pages were researched")).toBeVisible(); } },
  { name: "source detail, brand found", path: `/sources?audit_id=${IDS.complete}&source=${FOUND_SOURCE.id}`, ready: async (p) => { await settled(p); await expect(p.locator("mark")).toBeVisible(); } },
  { name: "source detail, page unreadable", path: `/sources?audit_id=${IDS.complete}&source=${UNREADABLE_SOURCE.id}`, ready: async (p) => { await settled(p); await expect(p.getByText("This page could not be read, so there is no snapshot to show")).toBeVisible(); } },
  { name: "evals with data", path: "/evals", ready: async (p) => { await settled(p); await expect(p.getByRole("table", { name: /Prompt versions compared/ })).toBeVisible(); } },
  { name: "evals, empty", path: "/evals", state: () => ({ ...appState(), evals: [] }), ready: async (p) => { await settled(p); await expect(p.getByRole("heading", { name: "No eval runs stored yet" })).toBeVisible(); } },
];

async function check(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  const report = results.violations.map((v) => ({
    rule: v.id,
    impact: v.impact,
    help: v.help,
    nodes: v.nodes.map((n) => ({ target: n.target.join(" "), summary: n.failureSummary?.split("\n").slice(0, 3).join(" | ") })),
  }));
  expect(report, JSON.stringify(report, null, 2)).toEqual([]);
}

for (const theme of THEMES) {
  test.describe(`axe, ${theme} theme`, () => {
    test.use({ colorScheme: theme });
    for (const c of CASES) {
      test(c.name, async ({ page }) => {
        await fakeApi.reset((c.state ?? appState)());
        await page.addInitScript((t) => window.localStorage.setItem("theme", t), theme);
        await page.goto(c.path);
        await c.ready(page);
        expect(await page.evaluate(() => document.documentElement.classList.contains("dark"))).toBe(theme === "dark");
        await check(page);
      });
    }

    test("review queue with the undo notice showing", async ({ page }) => {
      await fakeApi.reset();
      await page.addInitScript((t) => window.localStorage.setItem("theme", t), theme);
      await page.goto("/review");
      await settled(page);
      const form = page.getByRole("form", { name: /^Review:/ }).first();
      await form.getByLabel("Correct").check();
      await form.getByLabel(/^Reason/).fill("Because");
      await form.getByRole("button", { name: "Correct" }).click();
      await expect(page.getByRole("region", { name: "Review changes" }).getByText(/^Correct .* queued$/)).toBeVisible();
      await check(page);
    });

    test("new audit with validation errors", async ({ page }) => {
      await fakeApi.reset();
      await page.addInitScript((t) => window.localStorage.setItem("theme", t), theme);
      await page.goto("/audits/new");
      await page.getByRole("button", { name: "Start audit" }).click();
      await expect(page.getByRole("alert").first()).toBeVisible();
      await check(page);
    });
  });
}
