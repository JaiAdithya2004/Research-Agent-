/** Shared by playwright.config.ts and the specs. The key only exists to prove it never reaches the browser. */
export const WEB_PORT = Number(process.env.E2E_WEB_PORT ?? 3100);
export const FAKE_API_PORT = Number(process.env.E2E_API_PORT ?? 4010);
export const FAKE_API_URL = `http://127.0.0.1:${FAKE_API_PORT}`;
export const SECRET_API_KEY = "e2e-secret-key-7f3a9c1d5b8e4026";
