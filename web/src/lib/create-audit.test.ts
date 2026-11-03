import { describe, expect, it, vi } from "vitest";
import { createApi } from "@/lib/api/client";
import { createAudit, createKeyManager } from "./create-audit";
import type { AuditRequestBody } from "./audit-request";

const BODY: AuditRequestBody = {
  brand: "Acme",
  aliases: [],
  category: "crm",
  competitors: [],
  questions: ["q1"],
  repeats: 3,
  search_mode: "grounded",
  research_depth: "cited_pages",
};

const AUDIT = {
  id: "a1",
  status: "running",
  brand: "Acme",
  created_at: "2026-01-01T00:00:00Z",
  completed_at: null,
  degraded: false,
  caps_hit: [],
  request: BODY,
  summary: null,
};

function apiWith(status: number, body: unknown, headers: Record<string, string> = {}) {
  const fetchMock = vi.fn<typeof fetch>(
    async () => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } }),
  );
  return { api: createApi("http://localhost/api", fetchMock), fetchMock };
}

describe("createAudit", () => {
  it("sends the Idempotency-Key header and JSON body, and treats 202 as created", async () => {
    const { api, fetchMock } = apiWith(202, AUDIT);
    const out = await createAudit(api, BODY, "key-1");
    expect(out.kind).toBe("created");
    const req = fetchMock.mock.calls[0]![0] as Request;
    expect(req.method).toBe("POST");
    expect(req.url).toBe("http://localhost/api/audits");
    expect(req.headers.get("idempotency-key")).toBe("key-1");
    expect(await req.json()).toEqual(BODY);
  });

  it("treats 200 as a repeat of the original audit, not an error", async () => {
    const { api } = apiWith(200, AUDIT);
    const out = await createAudit(api, BODY, "key-1");
    expect(out).toMatchObject({ kind: "repeat", audit: { id: "a1" } });
  });

  it("maps 422 field errors from a validation list", async () => {
    const { api } = apiWith(422, {
      detail: [{ loc: ["body", "questions", 2], msg: "String should have at least 1 character", type: "x" }],
    });
    const out = await createAudit(api, BODY, "k");
    expect(out).toMatchObject({ kind: "invalid", fieldErrors: { "questions.2": "String should have at least 1 character" } });
  });

  it("keeps a string 422 detail as the message", async () => {
    const { api } = apiWith(422, { detail: "the Idempotency-Key was used with a different request" });
    const out = await createAudit(api, BODY, "k");
    expect(out).toMatchObject({ kind: "invalid", message: "the Idempotency-Key was used with a different request", fieldErrors: {} });
  });

  it("shows the server's 429 message and Retry-After", async () => {
    const { api } = apiWith(429, { detail: "daily budget reached" }, { "retry-after": "120" });
    const out = await createAudit(api, BODY, "k");
    expect(out).toEqual({ kind: "rate_limited", message: "daily budget reached", retryAfterSeconds: 120 });
  });

  it("maps 503 to unavailable and 401 to unauthorized", async () => {
    expect((await createAudit(apiWith(503, { detail: "could not queue" }).api, BODY, "k")).kind).toBe("unavailable");
    expect((await createAudit(apiWith(401, { detail: "no" }).api, BODY, "k")).kind).toBe("unauthorized");
  });

  it("reports a network failure as an error", async () => {
    const api = createApi("http://localhost/api", vi.fn<typeof fetch>(async () => Promise.reject(new TypeError("fetch failed"))));
    expect((await createAudit(api, BODY, "k")).kind).toBe("error");
  });
});

describe("createKeyManager", () => {
  it("reuses the key for an identical body and renews it when the body changes", () => {
    let n = 0;
    const keys = createKeyManager(() => `uuid-${++n}`);
    const first = keys.keyFor(BODY);
    expect(keys.keyFor({ ...BODY })).toBe(first); // retry or double click
    expect(keys.keyFor({ ...BODY, repeats: 2 })).not.toBe(first); // different body: the server would reject the old key
    keys.reset();
    expect(keys.keyFor(BODY)).not.toBe(first);
  });
});
