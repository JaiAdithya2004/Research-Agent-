import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FakeEventSource, step } from "@/test/fake-event-source";
import {
  AuditEventStream,
  auditEventsUrl,
  defaultBackoffMs,
  type ConnectionState,
  type StreamEvent,
} from "./audit-events";

function setup(isFinished: () => Promise<boolean> = async () => false) {
  const events: StreamEvent[] = [];
  const states: ConnectionState[] = [];
  const stream = new AuditEventStream({
    url: (last) => auditEventsUrl("a1", last),
    createSource: (url) => new FakeEventSource(url),
    onEvent: (e) => events.push(e),
    onState: (s) => states.push(s),
    isFinished,
    backoffMs: (attempt) => 100 * 2 ** attempt,
  });
  return { stream, events, states };
}

beforeEach(() => {
  FakeEventSource.reset();
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

describe("AuditEventStream", () => {
  it("connects to the BFF events URL and reports connecting, then live", () => {
    const { stream, states } = setup();
    stream.start();
    expect(FakeEventSource.last.url).toBe("/api/audits/a1/events");
    expect(states).toEqual(["connecting"]);
    FakeEventSource.last.open();
    expect(states).toEqual(["connecting", "live"]);
  });

  it("delivers events in the order received, with their ids and parsed data", () => {
    const { stream, events } = setup();
    stream.start();
    const es = FakeEventSource.last;
    es.open();
    es.emit("audit_started", 1, { planned_steps: 4 });
    es.emit("run_started", 2, { step: step(0, 0) });
    es.emit("run_verified", 3, { step: step(0, 0), verdict: "verified" });
    expect(events.map((e) => [e.id, e.type])).toEqual([
      [1, "audit_started"],
      [2, "run_started"],
      [3, "run_verified"],
    ]);
    expect(events[0]!.data).toEqual({ planned_steps: 4 });
    expect(stream.lastEventId).toBe(3);
  });

  it("never processes the same event id twice", () => {
    const { stream, events } = setup();
    stream.start();
    const es = FakeEventSource.last;
    es.emit("run_started", 5, {});
    es.emit("run_started", 5, {});
    es.emit("run_verified", 6, {});
    es.emit("run_started", 5, {}); // a late replay of an old id
    expect(events.map((e) => e.id)).toEqual([5, 6]);
  });

  it("while the browser is retrying by itself it shows reconnecting, then live again", () => {
    const { stream, states } = setup();
    stream.start();
    const es = FakeEventSource.last;
    es.open();
    es.fail({ closed: false });
    expect(states.at(-1)).toBe("reconnecting");
    expect(FakeEventSource.instances).toHaveLength(1); // native EventSource handles this retry
    es.open();
    expect(states.at(-1)).toBe("live");
  });

  it("when the browser gives up on a running audit it reconnects with ?last_event_id after a backoff", async () => {
    const { stream, events, states } = setup(async () => false);
    stream.start();
    const first = FakeEventSource.last;
    first.open();
    first.emit("run_started", 1, {});
    first.emit("run_verified", 2, {});
    first.fail({ closed: true });
    expect(states.at(-1)).toBe("reconnecting");
    await vi.advanceTimersByTimeAsync(99);
    expect(FakeEventSource.instances).toHaveLength(1); // still backing off
    await vi.advanceTimersByTimeAsync(1);
    expect(FakeEventSource.instances).toHaveLength(2);
    const second = FakeEventSource.last;
    expect(second.url).toBe("/api/audits/a1/events?last_event_id=2");
    second.open();
    expect(states.at(-1)).toBe("live");
    second.emit("run_verified", 2, {}); // overlap is ignored
    second.emit("run_verified", 3, {});
    expect(events.map((e) => e.id)).toEqual([1, 2, 3]);
  });

  it("backs off exponentially while the connection keeps failing, and resets once it opens", async () => {
    const { stream } = setup(async () => false);
    stream.start();
    FakeEventSource.last.fail({ closed: true });
    await vi.advanceTimersByTimeAsync(100);
    expect(FakeEventSource.instances).toHaveLength(2);
    FakeEventSource.last.fail({ closed: true });
    await vi.advanceTimersByTimeAsync(199);
    expect(FakeEventSource.instances).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(FakeEventSource.instances).toHaveLength(3); // 200 ms the second time
    FakeEventSource.last.open();
    FakeEventSource.last.fail({ closed: true });
    await vi.advanceTimersByTimeAsync(100);
    expect(FakeEventSource.instances).toHaveLength(4); // reset to 100 ms
  });

  it("the default backoff doubles and is capped at 30 seconds", () => {
    expect([0, 1, 2, 3].map(defaultBackoffMs)).toEqual([1000, 2000, 4000, 8000]);
    expect(defaultBackoffMs(20)).toBe(30_000);
  });

  it("stops on a terminal event and never reconnects", async () => {
    const { stream, events, states } = setup();
    stream.start();
    const es = FakeEventSource.last;
    es.open();
    es.emit("run_verified", 1, {});
    es.emit("audit_completed", 2, { status: "complete" });
    expect(events.map((e) => e.type)).toEqual(["run_verified", "audit_completed"]);
    expect(states.at(-1)).toBe("finished");
    expect(es.closed).toBe(true);
    es.emit("run_verified", 3, {}); // nothing is processed after the end
    es.fail({ closed: true });
    await vi.advanceTimersByTimeAsync(60_000);
    expect(events).toHaveLength(2);
    expect(FakeEventSource.instances).toHaveLength(1);
  });

  it.each(["audit_capped", "audit_failed"])("treats %s as terminal", (type) => {
    const { stream, states } = setup();
    stream.start();
    FakeEventSource.last.emit(type, 1, {});
    expect(states.at(-1)).toBe("finished");
  });

  it("stops when the connection closes and the audit is already finished (the server's 204)", async () => {
    const isFinished = vi.fn(async () => true);
    const { stream, states } = setup(isFinished);
    stream.start();
    FakeEventSource.last.fail({ closed: true });
    await vi.advanceTimersByTimeAsync(0);
    expect(isFinished).toHaveBeenCalledTimes(1);
    expect(states.at(-1)).toBe("finished");
    await vi.advanceTimersByTimeAsync(60_000);
    expect(FakeEventSource.instances).toHaveLength(1);
  });

  it("keeps trying when it cannot tell whether the audit is finished", async () => {
    const { stream } = setup(async () => {
      throw new Error("network");
    });
    stream.start();
    FakeEventSource.last.fail({ closed: true });
    await vi.advanceTimersByTimeAsync(100);
    expect(FakeEventSource.instances).toHaveLength(2);
    stream.stop();
  });

  it("stop() closes the connection and cancels a pending reconnect", async () => {
    const { stream, states } = setup(async () => false);
    stream.start();
    const es = FakeEventSource.last;
    es.fail({ closed: true });
    await vi.advanceTimersByTimeAsync(0);
    stream.stop();
    const before = states.length;
    await vi.advanceTimersByTimeAsync(60_000);
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(es.closed).toBe(true);
    expect(states).toHaveLength(before);
  });

  it("ignores events and errors from a connection that was replaced", async () => {
    const { stream, events } = setup(async () => false);
    stream.start();
    const old = FakeEventSource.last;
    old.fail({ closed: true });
    await vi.advanceTimersByTimeAsync(100);
    old.emit("run_started", 9, {});
    expect(events).toEqual([]);
    expect(FakeEventSource.instances).toHaveLength(2);
  });

  it("survives a malformed payload and still counts its id", () => {
    const { stream, events } = setup();
    stream.start();
    const es = FakeEventSource.last;
    const bad = new MessageEvent("run_started", { data: "not json", lastEventId: "4" });
    // reach the registered listener through emit's path
    (es as unknown as { listeners: Map<string, ((e: MessageEvent) => void)[]> }).listeners.get("run_started")![0]!(bad);
    expect(events).toEqual([{ id: 4, type: "run_started", data: {} }]);
    es.emit("run_started", 4, {});
    expect(events).toHaveLength(1);
  });
});
