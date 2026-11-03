import { expect, test as base, type Page } from "@playwright/test";
import { FAKE_API_URL, SECRET_API_KEY } from "./env";
import { appState, type FakeState } from "./fixtures";

export interface LoggedRequest {
  method: string;
  path: string;
  query: Record<string, string>;
  headers: { "idempotency-key": string | null; "last-event-id": string | null; "x-api-key-ok": boolean; cookie: string | null };
  body: unknown;
}

async function control(path: string, body?: unknown): Promise<unknown> {
  const res = await fetch(`${FAKE_API_URL}/__control/${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`fake API control ${path} failed: ${res.status}`);
  return res.json();
}

export const fakeApi = {
  reset: (state: FakeState = appState()) => control("reset", state),
  log: () => control("log") as Promise<LoggedRequest[]>,
  emit: (auditId: string, count = 1) => control("emit", { audit_id: auditId, count }),
  drop: (auditId: string) => control("drop", { audit_id: auditId }),
  async requests(method: string, pathPattern: RegExp): Promise<LoggedRequest[]> {
    return (await this.log()).filter((r) => r.method === method && pathPattern.test(r.path));
  },
};

/**
 * Collects every byte the browser receives (documents, scripts, JSON, event streams) so a test can
 * prove the API key never reached it.
 */
export function watchResponses(page: Page): { bodies: string[]; headers: string[] } {
  const seen = { bodies: [] as string[], headers: [] as string[] };
  page.on("response", async (res) => {
    seen.headers.push(JSON.stringify(res.headers()));
    const type = res.headers()["content-type"] ?? "";
    if (type.includes("event-stream")) return; // never ends; the stream is covered by its decoded events
    try {
      seen.bodies.push((await res.body()).toString("utf8"));
    } catch {
      // redirects and aborted requests have no body
    }
  });
  return seen;
}

export function expectNoSecret(seen: { bodies: string[]; headers: string[] }): void {
  expect(seen.bodies.length).toBeGreaterThan(0);
  for (const text of [...seen.bodies, ...seen.headers]) expect(text).not.toContain(SECRET_API_KEY);
}

export const test = base.extend<{ seen: { bodies: string[]; headers: string[] } }>({
  seen: async ({ page }, provide) => {
    await provide(watchResponses(page));
  },
});
export { expect };
