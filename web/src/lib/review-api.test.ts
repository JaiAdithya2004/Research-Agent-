import { beforeEach, describe, expect, it, vi } from "vitest";
import { json } from "@/test/utils";
import type { PendingReview } from "./review-outbox";
import { reviewMatches, sendReview, verifyReview } from "./review-api";

const fetchMock = vi.fn<typeof fetch>();
beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

const T0 = Date.parse("2026-03-01T10:00:00Z");
function entry(over: Partial<PendingReview> = {}): PendingReview {
  return {
    id: "e1",
    runId: "run-1",
    label: "Q",
    request: { action: "correct", corrected_value: "position=2", reason: "it is second" },
    createdAt: T0,
    dueAt: T0 + 5000,
    status: "sending",
    ownerTab: "t",
    sendingAt: T0 + 5000,
    sendingBy: "t",
    attempts: 1,
    verifyFirst: false,
    error: null,
    retryable: true,
    ...over,
  };
}

describe("sendReview", () => {
  it("POSTs the request body to /mentions/{run}/review with no reviewer field", async () => {
    fetchMock.mockResolvedValue(json(201, { id: "r1" }));
    expect(await sendReview(entry(), { keepalive: false })).toEqual({ kind: "ok" });
    const req = fetchMock.mock.calls[0]![0] as Request;
    expect(req.method).toBe("POST");
    expect(new URL(req.url).pathname).toBe("/api/mentions/run-1/review");
    expect(await req.clone().json()).toEqual({ action: "correct", corrected_value: "position=2", reason: "it is second" });
  });

  it("passes keepalive through", async () => {
    fetchMock.mockResolvedValue(json(201, {}));
    await sendReview(entry(), { keepalive: true });
    expect((fetchMock.mock.calls[0]![0] as Request).keepalive).toBe(true);
  });

  it.each([404, 409, 422])("%s is rejected with the server message, not retried", async (status) => {
    fetchMock.mockResolvedValue(json(status, { detail: "nothing to review" }));
    expect(await sendReview(entry(), { keepalive: false })).toEqual({ kind: "rejected", message: "nothing to review" });
  });

  it.each([401, 429, 500, 503])("%s is retryable", async (status) => {
    fetchMock.mockResolvedValue(json(status, { detail: "later" }));
    const r = await sendReview(entry(), { keepalive: false });
    expect(r.kind).toBe("retry");
  });

  it("a network error is retryable", async () => {
    fetchMock.mockRejectedValue(new TypeError("offline"));
    expect((await sendReview(entry(), { keepalive: false })).kind).toBe("retry");
  });
});

describe("reviewMatches", () => {
  const review = {
    action: "correct" as const,
    corrected_value: "position=2",
    reason: "it is second",
    reviewer: "dev",
    timestamp: "2026-03-01T10:00:06Z",
  };
  it("matches the same decision recorded after the click", () => {
    expect(reviewMatches(review, entry())).toBe(true);
  });
  it("does not match a different decision or an old identical one", () => {
    expect(reviewMatches({ ...review, reason: "other" }, entry())).toBe(false);
    expect(reviewMatches({ ...review, action: "approve", corrected_value: null }, entry())).toBe(false);
    expect(reviewMatches({ ...review, timestamp: "2026-02-01T10:00:00Z" }, entry())).toBe(false);
    expect(reviewMatches(null, entry())).toBe(false);
  });
});

describe("verifyReview", () => {
  it("delivered when the run's latest review is this one", async () => {
    fetchMock.mockResolvedValue(
      json(200, {
        review: { action: "correct", corrected_value: "position=2", reason: "it is second", reviewer: "dev", timestamp: "2026-03-01T10:00:06Z" },
      }),
    );
    expect(await verifyReview(entry())).toBe("delivered");
  });
  it("not delivered when there is no review", async () => {
    fetchMock.mockResolvedValue(json(200, { review: null }));
    expect(await verifyReview(entry())).toBe("not_delivered");
  });
  it("unknown when the lookup fails", async () => {
    fetchMock.mockResolvedValue(json(500, { detail: "x" }));
    expect(await verifyReview(entry())).toBe("unknown");
    fetchMock.mockRejectedValue(new TypeError("offline"));
    expect(await verifyReview(entry())).toBe("unknown");
  });
});
