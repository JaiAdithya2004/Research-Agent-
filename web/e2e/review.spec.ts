import type { Page } from "@playwright/test";
import { expect, expectNoSecret, fakeApi, test } from "./helpers";
import { IDS, QUESTIONS } from "./fixtures";

const REVIEW_PATH = new RegExp(`^/mentions/${IDS.reviewRun}/review$`);
const reviewPosts = () => fakeApi.requests("POST", /^\/mentions\/.+\/review$/);

test.beforeEach(async () => {
  await fakeApi.reset();
});

function card(page: Page, question = QUESTIONS[0]!) {
  return page.getByRole("form", { name: `Review: ${question}` });
}

async function approve(page: Page, reason: string) {
  const form = card(page);
  await form.getByLabel(/^Reason/).fill(reason);
  await form.getByRole("button", { name: "Approve" }).click();
}

test("approve with a reason, see the undo notice, undo it: nothing was POSTed", async ({ page, seen }) => {
  await page.goto("/review");
  await expect(page.getByText("2 flagged mentions loaded")).toBeVisible();

  // A reason is required: nothing is queued without one.
  await card(page).getByRole("button", { name: "Approve" }).click();
  await expect(card(page).getByText(/Give a reason/)).toBeVisible();
  await expect(page.getByTestId("review-announcer")).toHaveText("");

  await approve(page, "The quote is in the answer");
  const tray = page.getByRole("region", { name: "Review changes" });
  await expect(tray.getByText("Approve queued")).toBeVisible();
  await expect(page.getByTestId("review-announcer")).toContainText("Approval queued. You can undo it for 5 seconds.");
  // Optimistic: the card left the list while the decision waits.
  await expect(card(page)).toHaveCount(0);
  await expect(page.getByText("2 flagged mentions loaded")).toHaveCount(0);

  await tray.getByRole("button", { name: /^Undo approve/ }).click();
  await expect(page.getByTestId("review-announcer")).toHaveText("Undone. Nothing was sent.");
  await expect(card(page)).toBeVisible(); // the item is back

  // Wait out what would have been the window (5 s) plus margin: still nothing sent.
  await page.waitForTimeout(6500);
  expect(await reviewPosts()).toHaveLength(0);
  expectNoSecret(seen);
});

test("approve again and let the 5 seconds pass: exactly one POST, with the reason", async ({ page }) => {
  await page.goto("/review");
  await approve(page, "Verified by hand");
  const tray = page.getByRole("region", { name: "Review changes" });
  await expect(tray.getByText("Approve queued")).toBeVisible();

  // Inside the window nothing has been sent.
  await page.waitForTimeout(2000);
  expect(await reviewPosts()).toHaveLength(0);

  await expect.poll(async () => (await reviewPosts()).length, { timeout: 10_000 }).toBe(1);
  await expect(page.getByTestId("review-announcer")).toHaveText("Review saved.");
  await expect(tray.getByText("Approve queued")).toHaveCount(0);
  await page.waitForTimeout(1500); // and no second one follows
  const posts = await reviewPosts();
  expect(posts).toHaveLength(1);
  expect(posts[0]!.path).toMatch(REVIEW_PATH);
  expect(posts[0]!.body).toEqual({ action: "approve", reason: "Verified by hand" });
  expect(posts[0]!.headers["x-api-key-ok"]).toBe(true);

  // The saved review is gone from the queue; the other item stays.
  await page.reload();
  await expect(page.getByText("1 flagged mention loaded")).toBeVisible();
  await expect(card(page)).toHaveCount(0);
  await expect(card(page, QUESTIONS[1]!)).toBeVisible();
});

test("reloading during the undo window still results in exactly one POST", async ({ page }) => {
  await page.goto("/review");
  await approve(page, "Looks right");
  await expect(page.getByRole("region", { name: "Review changes" }).getByText("Approve queued")).toBeVisible();
  await page.reload();

  await expect.poll(async () => (await reviewPosts()).length, { timeout: 15_000 }).toBeGreaterThanOrEqual(1);
  // Give any duplicate (a re-send after the reload) time to show up.
  await page.waitForTimeout(8000);
  const posts = await reviewPosts();
  expect(posts).toHaveLength(1);
  expect(posts[0]!.body).toEqual({ action: "approve", reason: "Looks right" });
  await expect(page.getByRole("region", { name: "Review changes" }).getByText("Approve queued")).toHaveCount(0);
  await expect(card(page)).toHaveCount(0); // it was reviewed, so it is not in the queue any more
});
