import { expect, expectNoSecret, fakeApi, test } from "./helpers";
import { EVENT_COUNT, IDS, PLANNED } from "./fixtures";

const ID = IDS.running;
const progress = (page: import("@playwright/test").Page) => page.getByTestId("progress-text");
const announcement = (page: import("@playwright/test").Page) => page.getByTestId("progress-announcement");

test.beforeEach(async () => {
  await fakeApi.reset();
});

test("watch an audit complete from a scripted SSE stream", async ({ page, seen }) => {
  await page.goto(`/audits/${ID}`);
  await expect(page.getByRole("heading", { level: 1, name: "Acme" })).toBeVisible();
  await expect(page.getByTestId("connection-state")).toContainText("Live");
  await expect(page.getByText("Waiting for the first answers")).toBeVisible();

  // audit_started carries the plan.
  await fakeApi.emit(ID, 1);
  await expect(progress(page)).toHaveText(`0 of ${PLANNED} runs finished`);

  // run_started + run_verified for three runs.
  await fakeApi.emit(ID, 6);
  await expect(progress(page)).toHaveText(`3 of ${PLANNED} runs finished`);
  await expect(announcement(page)).toHaveText(`3 of ${PLANNED} runs finished`);
  await expect(page.getByRole("progressbar", { name: "Audit progress" })).toHaveAttribute("aria-valuenow", "3");
  await expect(page.getByRole("progressbar", { name: "Audit progress" })).toHaveAttribute("aria-valuemax", String(PLANNED));
  // The announcement is a polite live region.
  await expect(announcement(page)).toHaveAttribute("aria-live", "polite");

  // The other three runs, then audit_completed.
  await fakeApi.emit(ID, EVENT_COUNT - 7);
  await expect(page.getByTestId("connection-state")).toContainText("Finished");
  await expect(page.getByTestId("progress-text")).toHaveCount(0); // the progress panel is only for running audits
  await expect(page.getByRole("heading", { name: "Scorecard" })).toBeVisible();
  await expect(page.getByTestId("metric-mention-rate")).toContainText("50%");
  await expect(page.getByText("Complete", { exact: true }).first()).toBeVisible();
  expectNoSecret(seen);
});

test("a dropped stream reconnects with Last-Event-ID and neither repeats nor misses events", async ({ page }) => {
  await page.goto(`/audits/${ID}`);
  await expect(page.getByTestId("connection-state")).toContainText("Live");

  await fakeApi.emit(ID, 5); // audit_started + 2 finished runs (events 1..5)
  await expect(progress(page)).toHaveText(`2 of ${PLANNED} runs finished`);

  await fakeApi.drop(ID);
  await expect(page.getByTestId("connection-state")).toContainText(/Reconnecting|Live/);

  // A second connection must arrive, resuming after event 5.
  await expect
    .poll(async () => (await fakeApi.requests("GET", /\/events$/)).length, { timeout: 15_000 })
    .toBeGreaterThanOrEqual(2);
  const connections = await fakeApi.requests("GET", /\/events$/);
  const resumed = connections.at(-1)!;
  const resumeId = resumed.headers["last-event-id"] ?? resumed.query.last_event_id;
  expect(resumeId).toBe("5");
  await expect(page.getByTestId("connection-state")).toContainText("Live");

  // Events released while it was down arrive once; nothing is counted twice or lost.
  await fakeApi.emit(ID, 4); // events 6..9: two more finished runs
  await expect(progress(page)).toHaveText(`4 of ${PLANNED} runs finished`);

  // And a second drop right away, then the rest.
  await fakeApi.drop(ID);
  await fakeApi.emit(ID, EVENT_COUNT);
  await expect(page.getByTestId("connection-state")).toContainText("Finished", { timeout: 20_000 });
  await expect(page.getByRole("heading", { name: "Scorecard" })).toBeVisible();
  await expect(page.getByTestId("metric-mention-rate")).toContainText("50%");
});
