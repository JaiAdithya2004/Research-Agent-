import type { EventSourceLike } from "@/lib/audit-events";

/** A controllable stand-in for the browser's EventSource. */
export class FakeEventSource implements EventSourceLike {
  static instances: FakeEventSource[] = [];
  static reset(): void {
    FakeEventSource.instances = [];
  }
  static get last(): FakeEventSource {
    const last = FakeEventSource.instances[FakeEventSource.instances.length - 1];
    if (!last) throw new Error("no EventSource was created");
    return last;
  }

  readyState = 0;
  onopen: ((ev: Event) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  closed = false;
  private listeners = new Map<string, ((ev: MessageEvent) => void)[]>();

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (ev: MessageEvent) => void): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  close(): void {
    this.closed = true;
    this.readyState = 2;
  }

  /** The server accepted the connection. */
  open(): void {
    this.readyState = 1;
    this.onopen?.(new Event("open"));
  }

  /** The server sends one frame, like `id: 3\nevent: run_verified\ndata: {...}`. */
  emit(type: string, id: number | string, data: Record<string, unknown> = {}): void {
    const payload = JSON.stringify({ seq: typeof id === "number" ? id : undefined, type, data });
    const ev = new MessageEvent(type, { data: payload, lastEventId: String(id) });
    for (const l of this.listeners.get(type) ?? []) l(ev);
  }

  /** The connection dropped. `closed: true` means the browser gave up (readyState CLOSED). */
  fail(opts: { closed: boolean }): void {
    this.readyState = opts.closed ? 2 : 0;
    this.onerror?.(new Event("error"));
  }
}

export function step(question_idx: number, repeat_idx: number, extra: Record<string, unknown> = {}) {
  return { question_idx, provider: "gemini", model: "m", repeat_idx, search_mode: "grounded", ...extra };
}
