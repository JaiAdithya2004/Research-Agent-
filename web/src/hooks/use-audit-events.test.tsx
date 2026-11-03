import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FakeEventSource, step } from "@/test/fake-event-source";
import { makeAudit } from "@/test/fixtures";
import { json } from "@/test/utils";
import { useAuditEvents } from "./use-audit-events";

const fetchMock = vi.fn<typeof fetch>();
let client: QueryClient;

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  FakeEventSource.reset();
  fetchMock.mockReset();
  fetchMock.mockImplementation(async () => json(200, makeAudit({ status: "running" }, null)));
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("EventSource", FakeEventSource);
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});
afterEach(() => vi.useRealTimers());

describe("useAuditEvents", () => {
  it("does not connect while disabled", () => {
    renderHook(() => useAuditEvents("a1", { enabled: false }), { wrapper });
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  it("connects to the BFF, goes live, counts progress and refetches the audit on each event", async () => {
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useAuditEvents("a1", { enabled: true, throttleMs: 0 }), { wrapper });
    expect(FakeEventSource.last.url).toBe("/api/audits/a1/events");
    expect(result.current.connection).toBe("connecting");

    act(() => FakeEventSource.last.open());
    expect(result.current.connection).toBe("live");

    act(() => {
      FakeEventSource.last.emit("audit_started", 1, { planned_steps: 4 });
      FakeEventSource.last.emit("run_verified", 2, { step: step(0, 0), verdict: "verified" });
      FakeEventSource.last.emit("run_verified", 2, { step: step(0, 0), verdict: "verified" }); // duplicate id
    });
    expect(result.current.progress.planned).toBe(4);
    expect(result.current.progress.finished.size).toBe(1);
    expect(invalidate).toHaveBeenCalledTimes(2); // the duplicate did nothing
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["audit", "a1"] });
  });

  it("folds a burst of events into one refetch when throttled", async () => {
    vi.useFakeTimers();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    renderHook(() => useAuditEvents("a1", { enabled: true, throttleMs: 500 }), { wrapper });
    act(() => {
      for (let i = 1; i <= 5; i++) FakeEventSource.last.emit("run_started", i, { step: step(i, 0) });
    });
    expect(invalidate).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(500);
    });
    expect(invalidate).toHaveBeenCalledTimes(1);
  });

  it("stops at a terminal event: finished, refetched at once, connection closed", () => {
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useAuditEvents("a1", { enabled: true, throttleMs: 60_000 }), { wrapper });
    act(() => {
      FakeEventSource.last.emit("audit_completed", 7, { status: "complete" });
    });
    expect(result.current.connection).toBe("finished");
    expect(invalidate).toHaveBeenCalledTimes(1);
    expect(FakeEventSource.last.closed).toBe(true);
  });

  it("closes the connection on unmount and when disabled", () => {
    const { rerender, unmount } = renderHook(({ enabled }) => useAuditEvents("a1", { enabled }), {
      wrapper,
      initialProps: { enabled: true },
    });
    const es = FakeEventSource.last;
    rerender({ enabled: false });
    expect(es.closed).toBe(true);
    rerender({ enabled: true });
    const second = FakeEventSource.last;
    expect(second).not.toBe(es);
    unmount();
    expect(second.closed).toBe(true);
  });

  it("when the browser gives up on a running audit it reconnects with the last id", async () => {
    const { result } = renderHook(() => useAuditEvents("a1", { enabled: true, throttleMs: 0 }), { wrapper });
    act(() => {
      FakeEventSource.last.open();
      FakeEventSource.last.emit("run_started", 1, {});
      FakeEventSource.last.emit("run_started", 2, {});
      FakeEventSource.last.fail({ closed: true });
    });
    expect(result.current.connection).toBe("reconnecting");
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(2), { timeout: 3000 });
    expect(FakeEventSource.last.url).toBe("/api/audits/a1/events?last_event_id=2");
  });

  it("stops for good when the connection closes and the audit turns out to be finished (204)", async () => {
    fetchMock.mockImplementation(async () => json(200, makeAudit({ status: "complete" })));
    const { result } = renderHook(() => useAuditEvents("a1", { enabled: true }), { wrapper });
    act(() => FakeEventSource.last.fail({ closed: true }));
    await waitFor(() => expect(result.current.connection).toBe("finished"));
    expect(FakeEventSource.instances).toHaveLength(1);
  });
});
