import { defineConfig, devices } from "@playwright/test";
import { FAKE_API_PORT, FAKE_API_URL, SECRET_API_KEY, WEB_PORT } from "./e2e/env";

/**
 * End-to-end tests against the real built Next app. The backend is NOT started: the Next server
 * is pointed (API_BASE_URL) at e2e/fake-api.mjs, so the real BFF proxy, the real generated client
 * and the real browser code all run. Port 3100 (not 3000) so a dev server can stay up.
 *
 * `npm run e2e` builds the app first (see webServer). One worker: the fake API holds one state.
 */
export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.spec.ts",
  outputDir: "./e2e/test-results",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [["list"], ["html", { open: "never", outputFolder: "./e2e/report" }]],
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: "node e2e/fake-api.mjs",
      url: `${FAKE_API_URL}/health`,
      reuseExistingServer: !process.env.CI,
      env: { FAKE_API_PORT: String(FAKE_API_PORT), FAKE_API_KEY: SECRET_API_KEY },
    },
    {
      command: `npm run build && npx next start -H 127.0.0.1 -p ${WEB_PORT}`,
      url: `http://127.0.0.1:${WEB_PORT}/`,
      reuseExistingServer: !process.env.CI,
      timeout: 300_000,
      env: { API_BASE_URL: FAKE_API_URL, AUDIT_API_KEY: SECRET_API_KEY },
    },
  ],
});
