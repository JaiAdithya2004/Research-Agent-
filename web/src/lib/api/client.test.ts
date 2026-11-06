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

  it("still rejects for real network errors", async () => {
    const api = createApi("http://localhost/api", vi.fn<typeof fetch>(() => Promise.reject(new TypeError("fetch failed"))));
    expect(await settled(api.GET("/health" as never, {} as never))).toBe("rejected");
  });
});
