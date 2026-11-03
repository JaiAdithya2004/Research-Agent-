import { expect, expectNoSecret, fakeApi, test } from "./helpers";
import { IDS } from "./fixtures";

test.beforeEach(async () => {
  await fakeApi.reset();
});

test("create an audit: the form sends an Idempotency-Key, 202 navigates to the audit", async ({ page, seen }) => {
  await page.goto("/audits/new");
  await page.getByLabel(/^Brand/).fill("Initech");
  await page.getByLabel(/^Category/).fill("crm");
  await page.getByLabel("Question 1", { exact: true }).fill("What is the best CRM for startups?");

  const posted = page.waitForRequest((r) => r.method() === "POST" && new URL(r.url()).pathname === "/api/audits");
  await page.getByRole("button", { name: "Start audit" }).click();
  const request = await posted;

  // The header leaves the browser, and the proxy passes it on to the API.
  const key = request.headers()["idempotency-key"];
  expect(key).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
  expect(request.postDataJSON()).toMatchObject({ brand: "Initech", category: "crm", questions: ["What is the best CRM for startups?"] });

  await expect(page).toHaveURL(new RegExp(`/audits/${IDS.created}$`));
  await expect(page.getByRole("heading", { level: 1, name: "Initech" })).toBeVisible();

  const [apiCall] = await fakeApi.requests("POST", /^\/audits$/);
  expect(apiCall?.headers["idempotency-key"]).toBe(key);
  expect(apiCall?.headers["x-api-key-ok"]).toBe(true); // the proxy added the server-side key
  expect(apiCall?.headers.cookie).toBeNull();
  expectNoSecret(seen);
});

test("create an audit: a validation error shows the summary and sends nothing", async ({ page }) => {
  await page.goto("/audits/new");
  await page.getByRole("button", { name: "Start audit" }).click();
  await expect(page.getByRole("alert").first()).toContainText(/Fix \d+ problems? to continue/);
  expect(await fakeApi.requests("POST", /^\/audits$/)).toHaveLength(0);
});
