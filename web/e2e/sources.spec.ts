import { expect, expectNoSecret, fakeApi, test } from "./helpers";
import { FOUND_SOURCE, IDS, NOT_FOUND_SOURCE, SNAPSHOT_QUOTE, UNREADABLE_SOURCE } from "./fixtures";

test.beforeEach(async () => {
  await fakeApi.reset();
});

test("open a source: the brand quote is highlighted and the snapshot time is shown", async ({ page, seen }) => {
  await page.goto("/sources");
  await page.locator(`a[href="/sources?audit_id=${IDS.complete}"]`).click(); // the audit picker
  await expect(page).toHaveURL(new RegExp(`audit_id=${IDS.complete}`));
  await expect(page.getByTestId("source-row")).toHaveCount(3);

  await page.getByRole("link", { name: new RegExp(`^Open ${FOUND_SOURCE.domain}`) }).click();
  await expect(page).toHaveURL(new RegExp(`source=${FOUND_SOURCE.id}`));
  const detail = page.getByRole("region", { name: FOUND_SOURCE.domain });
  await expect(detail.getByRole("heading", { level: 2, name: FOUND_SOURCE.domain })).toBeFocused();

  const mark = detail.locator("mark");
  await expect(mark).toHaveCount(1);
  await expect(mark).toContainText(SNAPSHOT_QUOTE);
  await expect(detail.getByTestId("snapshot-time")).toContainText(/Snapshot taken/);
  await expect(detail.getByTestId("snapshot-time").locator("time")).toHaveAttribute("datetime", FOUND_SOURCE.snapshot_fetched_at!);
  await expect(detail.locator('[data-evidence-kind="found"]')).toBeVisible();
  expectNoSecret(seen);
});

test("a page that could not be read says so and is never 'brand not found'", async ({ page }) => {
  await page.goto(`/sources?audit_id=${IDS.complete}&source=${UNREADABLE_SOURCE.id}`);
  const detail = page.getByRole("region", { name: UNREADABLE_SOURCE.domain });
  await expect(detail.getByText("This page could not be read, so there is no snapshot to show")).toBeVisible();
  await expect(detail.locator('[data-evidence-kind="unreadable"]')).toContainText("Could not read: unreachable");
  await expect(detail.locator('[data-evidence-kind="not_found"]')).toHaveCount(0);
  await expect(detail.locator('[data-evidence-kind="found"]')).toHaveCount(0);
  await expect(detail.getByText("Page read: brand not on it")).toHaveCount(0);
  await expect(detail.locator("mark")).toHaveCount(0);
  await expect(detail.getByText("This is not the same as the brand being absent.", { exact: false })).toBeVisible();

  // The same page in the table reads the same way.
  const row = page.getByTestId("source-row").filter({ hasText: UNREADABLE_SOURCE.domain });
  await expect(row.locator('[data-evidence-kind="unreadable"]')).toBeVisible();
  await expect(page.getByTestId("source-count")).toContainText("1 with the brand, 1 read without it, 1 could not be read");

  // Contrast: a page that WAS read without the brand is a different, labelled state.
  await page.goto(`/sources?audit_id=${IDS.complete}&source=${NOT_FOUND_SOURCE.id}`);
  const absent = page.getByRole("region", { name: NOT_FOUND_SOURCE.domain });
  await expect(absent.locator('[data-evidence-kind="not_found"]')).toContainText("Page read: brand not on it");
  await expect(absent.locator("mark")).toHaveCount(0);
});
