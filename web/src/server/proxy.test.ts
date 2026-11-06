// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import { buildTarget, proxyRequest } from "./proxy";

const CONFIG = { baseUrl: "http://backend.internal:8000", apiKey: "secret-key-123" };

function capture(upstream: Response) {
  return vi.fn<typeof fetch>(async () => upstream);
}

function sentHeaders(fn: ReturnType<typeof capture>): Headers {
  return new Headers(fn.mock.calls[0]![1]!.headers);
}

describe("proxyRequest", () => {
  it("adds the API key and forwards method, path and query", async () => {
    const f = capture(new Response("[]", { status: 200, headers: { "content-type": "application/json" } }));
    const res = await proxyRequest(
      new Request("http://localhost/api/audits?limit=5&status=running"),
      ["audits"],
      CONFIG,
      f,
    );
    expect(res.status).toBe(200);
    expect(f.mock.calls[0]![0]).toBe("http://backend.internal:8000/audits?limit=5&status=running");
    expect(sentHeaders(f).get("x-api-key")).toBe("secret-key-123");
    expect(f.mock.calls[0]![1]!.method).toBe("GET");
  });

  it("forwards the JSON body, Idempotency-Key and Last-Event-ID", async () => {
    const f = capture(new Response("{}", { status: 202 }));
    const body = JSON.stringify({ brand: "Acme" });
    await proxyRequest(
      new Request("http://localhost/api/audits", {
        method: "POST",
        body,
        headers: { "content-type": "application/json", "idempotency-key": "k-1", "last-event-id": "7" },
      }),
      ["audits"],
      CONFIG,
      f,
    );
    const h = sentHeaders(f);
    expect(h.get("idempotency-key")).toBe("k-1");
    expect(h.get("last-event-id")).toBe("7");
    expect(h.get("content-type")).toBe("application/json");
    expect(new TextDecoder().decode(f.mock.calls[0]![1]!.body as ArrayBuffer)).toBe(body);
  });

  it("strips cookies, authorization and a browser-supplied API key", async () => {
    const f = capture(new Response("{}"));
    await proxyRequest(
      new Request("http://localhost/api/audits", {
        headers: {
          cookie: "session=abc",
          authorization: "Bearer x",
          "x-api-key": "attacker",
          "x-forwarded-for": "1.2.3.4",
        },
      }),
      ["audits"],
      CONFIG,
      f,
    );
    const h = sentHeaders(f);
    expect(h.get("cookie")).toBeNull();
    expect(h.get("authorization")).toBeNull();
    expect(h.get("x-forwarded-for")).toBeNull();
    expect(h.get("x-api-key")).toBe("secret-key-123");
  });

  it("never returns the key or Set-Cookie to the browser", async () => {
    const f = capture(
      new Response("{}", {
        headers: { "set-cookie": "a=b", "x-api-key": "secret-key-123", "content-type": "application/json" },
      }),
    );
    const res = await proxyRequest(new Request("http://localhost/api/audits"), ["audits"], CONFIG, f);
    expect(res.headers.get("set-cookie")).toBeNull();
    expect(res.headers.get("x-api-key")).toBeNull();
    expect(JSON.stringify([...res.headers])).not.toContain("secret-key-123");
  });

  it("does not follow redirects, so the key never travels further", async () => {
    const f = capture(new Response(null, { status: 302, headers: { location: "http://evil.example/" } }));
    await proxyRequest(new Request("http://localhost/api/audits"), ["audits"], CONFIG, f);
    expect(f.mock.calls[0]![1]!.redirect).toBe("manual");
  });

  it("passes upstream errors through faithfully, including Retry-After", async () => {
    const f = capture(
      new Response(JSON.stringify({ detail: "daily budget reached" }), {
        status: 429,
        headers: { "content-type": "application/json", "retry-after": "3600" },
      }),
    );
    const res = await proxyRequest(
      new Request("http://localhost/api/audits", { method: "POST", body: "{}" }),
      ["audits"],
      CONFIG,
      f,
    );
    expect(res.status).toBe(429);
    expect(res.headers.get("retry-after")).toBe("3600");
    expect(await res.json()).toEqual({ detail: "daily budget reached" });
  });

  it.each([200, 202, 401, 404, 409, 422, 503])("preserves status %i", async (status) => {
    const f = capture(
      new Response(JSON.stringify({ detail: "x" }), { status, headers: { "content-type": "application/json" } }),
    );
    const res = await proxyRequest(new Request("http://localhost/api/audits/1"), ["audits", "1"], CONFIG, f);
    expect(res.status).toBe(status);
  });

  it("answers 204 with no body", async () => {
    const f = capture(new Response(null, { status: 204 }));
    const res = await proxyRequest(
      new Request("http://localhost/api/audits/1/events"),
      ["audits", "1", "events"],
      CONFIG,
      f,
    );
    expect(res.status).toBe(204);
    expect(res.body).toBeNull();
  });

  it("returns 502 without leaking internals when the backend is unreachable", async () => {
    const f = vi.fn(async () => {
      throw new Error("connect ECONNREFUSED backend.internal:8000");
    });
    const res = await proxyRequest(
      new Request("http://localhost/api/audits"),
      ["audits"],
      CONFIG,
      f as unknown as typeof fetch,
    );
    expect(res.status).toBe(502);
    const text = await res.text();
    expect(text).not.toContain("backend.internal");
    expect(text).not.toContain("secret-key-123");
  });

  it("fails closed when the key is not configured", async () => {
    const f = capture(new Response("{}"));
    const res = await proxyRequest(
      new Request("http://localhost/api/audits"),
      ["audits"],
      { baseUrl: CONFIG.baseUrl, apiKey: "" },
      f,
    );
    expect(res.status).toBe(500);
    expect(f).not.toHaveBeenCalled();
  });

  it("forwards DELETE for one audit, with the key and no body", async () => {
    const f = capture(new Response(null, { status: 204 }));
    const res = await proxyRequest(new Request("http://localhost/api/audits/abc-123", { method: "DELETE" }), ["audits", "abc-123"], CONFIG, f);
    expect(res.status).toBe(204);
    expect(f.mock.calls[0]![0]).toBe("http://backend.internal:8000/audits/abc-123");
    expect(f.mock.calls[0]![1]!.method).toBe("DELETE");
    expect(f.mock.calls[0]![1]!.body).toBeUndefined();
    expect(sentHeaders(f).get("x-api-key")).toBe("secret-key-123");
  });

  it("allows DELETE nowhere else", async () => {
    const f = capture(new Response("{}"));
    for (const path of [["audits"], ["audits", "a", "runs"], ["runs", "r"], ["mentions", "m", "review"], ["health"]]) {
      const res = await proxyRequest(new Request("http://localhost/api/x", { method: "DELETE" }), path, CONFIG, f);
      expect(res.status, path.join("/")).toBe(405);
    }
    expect(f).not.toHaveBeenCalled();
  });

  it("rejects other methods and path traversal", async () => {
    const f = capture(new Response("{}"));
    const put = await proxyRequest(new Request("http://localhost/api/audits/a", { method: "PUT" }), ["audits", "a"], CONFIG, f);
    expect(put.status).toBe(405);
    expect((await proxyRequest(new Request("http://localhost/api/x"), ["..", "admin"], CONFIG, f)).status).toBe(400);
    expect((await proxyRequest(new Request("http://localhost/api/x"), [], CONFIG, f)).status).toBe(400);
    expect(f).not.toHaveBeenCalled();
  });

  it("streams SSE without buffering: the first chunk arrives before the upstream ends", async () => {
    const enc = new TextEncoder();
    let finish!: () => void;
    const upstreamBody = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(enc.encode("id: 1\nevent: audit_started\ndata: {}\n\n"));
        finish = () => controller.close();
      },
    });
    const f = capture(new Response(upstreamBody, { headers: { "content-type": "text/event-stream" } }));
    const res = await proxyRequest(
      new Request("http://localhost/api/audits/1/events", { headers: { "last-event-id": "0" } }),
      ["audits", "1", "events"],
      CONFIG,
      f,
    );
    expect(res.headers.get("content-type")).toBe("text/event-stream");
    expect(res.headers.get("cache-control")).toContain("no-transform");
    expect(res.headers.get("x-accel-buffering")).toBe("no");
    const reader = res.body!.getReader();
    const first = await reader.read(); // resolves while the upstream is still open
    expect(new TextDecoder().decode(first.value)).toContain("audit_started");
    expect(first.done).toBe(false);
    finish();
    expect((await reader.read()).done).toBe(true);
    expect(sentHeaders(f).get("last-event-id")).toBe("0");
  });

  it("propagates the abort of the incoming request to the upstream request", async () => {
    const f = capture(new Response("{}"));
    const ctrl = new AbortController();
    await proxyRequest(new Request("http://localhost/api/audits", { signal: ctrl.signal }), ["audits"], CONFIG, f);
    const signal = f.mock.calls[0]![1]!.signal as AbortSignal;
    ctrl.abort();
    expect(signal.aborted).toBe(true);
  });
});

describe("buildTarget", () => {
  it("encodes segments and trims the base slash", () => {
    expect(buildTarget("http://h:1/", ["audits", "a b"], "?x=1")).toBe("http://h:1/audits/a%20b?x=1");
  });
  it("rejects dot segments and embedded slashes", () => {
    expect(buildTarget("http://h", ["a", "."], "")).toBeNull();
    expect(buildTarget("http://h", ["a/b"], "")).toBeNull();
  });
});
