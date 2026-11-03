import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ReviewOutbox,
  STORAGE_KEY,
  UNDO_MS,
  type OutboxDeps,
  type PendingReview,
  type SendResult,
  type VerifyResult,
} from "./review-outbox";

class MemoryStorage {
  data = new Map<string, string>();
  getItem(k: string) {
    return this.data.get(k) ?? null;
  }
  setItem(k: string, v: string) {
    this.data.set(k, v);
  }
}

let n = 0;
function make(storage: MemoryStorage, over: Partial<OutboxDeps> = {}, tabId = "tab-1") {
  const send = vi.fn<OutboxDeps["send"]>(async () => ({ kind: "ok" }) as SendResult);
  const verify = vi.fn<OutboxDeps["verify"]>(async () => "not_delivered" as VerifyResult);
  const onSettled = vi.fn();
  const outbox = new ReviewOutbox({
    storage,
    now: () => Date.now(),
    newId: () => `id-${++n}`,
    tabId,
    send,
    verify,
    onSettled,
    ...over,
  });
  return { outbox, send, verify, onSettled };
}

const input = { runId: "run-1", label: "Q one", request: { action: "approve" as const, reason: "ok" } };

function stored(storage: MemoryStorage): PendingReview[] {
  return JSON.parse(storage.getItem(STORAGE_KEY) ?? "[]") as PendingReview[];
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-03-01T10:00:00Z"));
  n = 0;
});
afterEach(() => vi.useRealTimers());

describe("ReviewOutbox undo window", () => {
  it("does not POST anything before 5 seconds have passed", async () => {
    const s = new MemoryStorage();
    const { outbox, send } = make(s);
    outbox.start();
    outbox.enqueue(input);
    await vi.advanceTimersByTimeAsync(UNDO_MS - 1);
    expect(send).not.toHaveBeenCalled();
    expect(outbox.getSnapshot().entries[0]!.status).toBe("waiting");
  });

  it("POSTs once after 5 seconds, then removes the entry", async () => {
    const s = new MemoryStorage();
    const { outbox, send, onSettled } = make(s);
    outbox.start();
    outbox.enqueue(input);
    await vi.advanceTimersByTimeAsync(UNDO_MS);
    expect(send).toHaveBeenCalledTimes(1);
    expect(send.mock.calls[0]![0]).toMatchObject({ runId: "run-1", request: input.request });
    expect(outbox.getSnapshot().entries).toHaveLength(0);
    expect(stored(s)).toHaveLength(0);
    expect(onSettled).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(send).toHaveBeenCalledTimes(1);
  });

  it("undo inside the window means nothing is ever sent", async () => {
    const s = new MemoryStorage();
    const { outbox, send } = make(s);
    outbox.start();
    const id = outbox.enqueue(input);
    await vi.advanceTimersByTimeAsync(3000);
    expect(outbox.undo(id)).toBe(true);
    expect(outbox.getSnapshot().entries).toHaveLength(0);
    expect(outbox.getSnapshot().notice?.text).toMatch(/nothing was sent/i);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(send).not.toHaveBeenCalled();
    expect(stored(s)).toHaveLength(0);
  });

  it("cannot undo once the window has passed", async () => {
    const s = new MemoryStorage();
    let release!: (r: SendResult) => void;
    const { outbox } = make(s, {
      send: () => new Promise<SendResult>((res) => (release = res)),
    });
    outbox.start();
    const id = outbox.enqueue(input);
    await vi.advanceTimersByTimeAsync(UNDO_MS);
    expect(outbox.undo(id)).toBe(false);
    release({ kind: "ok" });
  });

  it("a newer decision for the same run replaces one still waiting (latest wins, one POST)", async () => {
    const s = new MemoryStorage();
    const { outbox, send } = make(s);
    outbox.start();
    outbox.enqueue(input);
    await vi.advanceTimersByTimeAsync(2000);
    outbox.enqueue({ ...input, request: { action: "reject", reason: "changed my mind" } });
    await vi.advanceTimersByTimeAsync(UNDO_MS);
    expect(send).toHaveBeenCalledTimes(1);
    expect(send.mock.calls[0]![0].request.action).toBe("reject");
  });
});

describe("ReviewOutbox persistence", () => {
  it("persists the queue, so a reload inside the window resumes it and still sends exactly once", async () => {
    const s = new MemoryStorage();
    const first = make(s);
    first.outbox.start();
    first.outbox.enqueue(input);
    await vi.advanceTimersByTimeAsync(2000);
    expect(stored(s)).toHaveLength(1);

    // "Reload": the old page is gone (stop), a new outbox starts over the same storage and tab id.
    first.outbox.stop();
    const second = make(s);
    second.outbox.start();
    expect(second.outbox.getSnapshot().entries).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(UNDO_MS);
    expect(second.send).toHaveBeenCalledTimes(1);
    expect(first.send).not.toHaveBeenCalled(); // the old instance's timers were stopped by its own stop()
  });

  it("an entry that came due while the page was closed is sent on the next load", async () => {
    const s = new MemoryStorage();
    const a = make(s);
    a.outbox.start();
    a.outbox.enqueue(input);
    a.outbox.stop();
    vi.setSystemTime(new Date("2026-03-01T10:30:00Z"));
    const b = make(s);
    b.outbox.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(b.send).toHaveBeenCalledTimes(1);
  });

  it("works in memory when storage throws", async () => {
    const broken = {
      getItem: () => {
        throw new Error("denied");
      },
      setItem: () => {
        throw new Error("denied");
      },
    };
    const { outbox, send } = make(broken as unknown as MemoryStorage);
    outbox.start();
    outbox.enqueue(input);
    await vi.advanceTimersByTimeAsync(UNDO_MS);
    expect(send).toHaveBeenCalledTimes(1);
  });

  it("ignores corrupt stored data", () => {
    const s = new MemoryStorage();
    s.setItem(STORAGE_KEY, "{not json");
    const { outbox } = make(s);
    outbox.start();
    expect(outbox.getSnapshot().entries).toEqual([]);
    s.setItem(STORAGE_KEY, JSON.stringify([{ id: 1 }]));
    const again = make(s);
    again.outbox.start();
    expect(again.outbox.getSnapshot().entries).toEqual([]);
  });
});

describe("ReviewOutbox no double send", () => {
  it("flushes waiting entries on pagehide (keepalive) and does not send them again", async () => {
    const s = new MemoryStorage();
    const { outbox, send } = make(s);
    outbox.start();
    outbox.enqueue(input);
    await vi.advanceTimersByTimeAsync(1000);
    window.dispatchEvent(new Event("pagehide"));
    expect(send).toHaveBeenCalledTimes(1);
    expect(send.mock.calls[0]![1]).toEqual({ keepalive: true });
    await vi.advanceTimersByTimeAsync(UNDO_MS * 2);
    expect(send).toHaveBeenCalledTimes(1);
  });

  it("after a reload mid-send it first asks the server whether the review landed, and does not resend if so", async () => {
    const s = new MemoryStorage();
    const a = make(s, { send: () => new Promise<SendResult>(() => undefined) });
    a.outbox.start();
    a.outbox.enqueue(input);
    await vi.advanceTimersByTimeAsync(UNDO_MS);
    expect(stored(s)[0]!.status).toBe("sending");
    a.outbox.stop();

    const delivered = vi.fn<OutboxDeps["verify"]>(async () => "delivered");
    const b = make(s, { verify: delivered });
    b.outbox.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(delivered).toHaveBeenCalledTimes(1);
    expect(b.send).not.toHaveBeenCalled();
    expect(b.outbox.getSnapshot().entries).toHaveLength(0);
    expect(stored(s)).toHaveLength(0);
  });

  it("resends after a reload mid-send only when the server does not have the review", async () => {
    const s = new MemoryStorage();
    const a = make(s, { send: () => new Promise<SendResult>(() => undefined) });
    a.outbox.start();
    a.outbox.enqueue(input);
    await vi.advanceTimersByTimeAsync(UNDO_MS);
    a.outbox.stop();
    const b = make(s);
    b.outbox.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(b.verify).toHaveBeenCalledTimes(1);
    expect(b.send).toHaveBeenCalledTimes(1);
  });

  it("another tab's fresh send is left alone", async () => {
    const s = new MemoryStorage();
    const a = make(s, { send: () => new Promise<SendResult>(() => undefined) }, "tab-A");
    a.outbox.start();
    a.outbox.enqueue(input);
    await vi.advanceTimersByTimeAsync(UNDO_MS);
    const b = make(s, {}, "tab-B");
    b.outbox.start();
    await vi.advanceTimersByTimeAsync(1000);
    expect(b.send).not.toHaveBeenCalled();
    expect(b.verify).not.toHaveBeenCalled();
  });

  it("another tab does not send an entry the owner tab will send, within the grace period", async () => {
    const s = new MemoryStorage();
    const a = make(s, {}, "tab-A");
    a.outbox.start();
    a.outbox.enqueue(input);
    const b = make(s, {}, "tab-B");
    b.outbox.start();
    await vi.advanceTimersByTimeAsync(UNDO_MS + 1);
    expect(a.send).toHaveBeenCalledTimes(1);
    expect(b.send).not.toHaveBeenCalled();
  });
});

describe("ReviewOutbox failures", () => {
  it("a rejected review (409, 422, 404) is kept as failed and is not retryable", async () => {
    const s = new MemoryStorage();
    const { outbox, send } = make(s, { send: async () => ({ kind: "rejected", message: "This run has no mention to review." }) });
    outbox.start();
    outbox.enqueue(input);
    await vi.advanceTimersByTimeAsync(UNDO_MS);
    const e = outbox.getSnapshot().entries[0]!;
    expect(e).toMatchObject({ status: "failed", retryable: false, error: "This run has no mention to review." });
    await vi.advanceTimersByTimeAsync(60_000);
    expect(send).not.toBeUndefined();
    expect(outbox.getSnapshot().entries).toHaveLength(1);
  });

  it("a network failure is kept, retryable, and retry verifies before sending again", async () => {
    const s = new MemoryStorage();
    const sendFn = vi.fn<OutboxDeps["send"]>();
    sendFn.mockResolvedValueOnce({ kind: "retry", message: "offline" });
    sendFn.mockResolvedValue({ kind: "ok" });
    const { outbox, verify } = make(s, { send: sendFn });
    outbox.start();
    const id = outbox.enqueue(input);
    await vi.advanceTimersByTimeAsync(UNDO_MS);
    expect(outbox.getSnapshot().entries[0]).toMatchObject({ status: "failed", retryable: true });
    outbox.retry(id);
    await vi.advanceTimersByTimeAsync(0);
    expect(verify).toHaveBeenCalledTimes(1);
    expect(sendFn).toHaveBeenCalledTimes(2);
    expect(outbox.getSnapshot().entries).toHaveLength(0);
  });

  it("discard removes a failed entry", async () => {
    const s = new MemoryStorage();
    const { outbox } = make(s, { send: async () => ({ kind: "rejected", message: "no" }) });
    outbox.start();
    const id = outbox.enqueue(input);
    await vi.advanceTimersByTimeAsync(UNDO_MS);
    outbox.discard(id);
    expect(outbox.getSnapshot().entries).toHaveLength(0);
  });
});
