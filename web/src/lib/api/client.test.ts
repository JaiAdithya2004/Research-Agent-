import { describe, expect, it, vi } from "vitest";
import { createApi } from "./client";

const settled = (p: Promise<unknown>) =>
  Promise.race([p.then(() => "resolved", () => "rejected"), new Promise((r) => setTimeout(() => r("pending"), 20))]);

describe("createApi and cancelled requests", () => {
  it("does not reject when a request is cancelled (nobody is waiting for it any more)", async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn<typeof fetch>(
      (input) =>
        new Promise((_, reject) => {
          (input as Request).signal.addEventListener("abort", () => reject(controller.signal.reason ?? new DOMException("aborted", "AbortError")));
        }),
    );
    const api = createApi("http://localhost/api", fetchMock);
    const call = api.GET("/health" as never, { signal: controller.signal } as never);
    controller.abort();
    expect(await settled(call)).toBe("pending");
  });

  it("does not reject when the request is cancelled while its body is still being read", async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn<typeof fetch>(async (input) => {
      const body = () =>
        new Promise<string>((_, reject) => {
          (input as Request).signal.addEventListener("abort", () => reject(new DOMException("signal is aborted without reason", "AbortError")));
        });
      const res = new Response("{}", { headers: { "content-type": "application/json" } });
      for (const m of ["json", "text", "blob", "arrayBuffer"] as const) {
        Object.defineProperty(res, m, { value: body, configurable: true, writable: true });
      }
      return res;
    });
    const api = createApi("http://localhost/api", fetchMock);
    const call = api.GET("/health" as never, { signal: controller.signal } as never);
    setTimeout(() => controller.abort(), 5);
    expect(await settled(call)).toBe("pending");
  });

  it("still rejects for real network errors", async () => {
    const api = createApi("http://localhost/api", vi.fn<typeof fetch>(() => Promise.reject(new TypeError("fetch failed"))));
    expect(await settled(api.GET("/health" as never, {} as never))).toBe("rejected");
  });
});
