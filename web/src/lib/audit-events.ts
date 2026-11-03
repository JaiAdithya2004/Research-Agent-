/**
 * The live-progress stream for one audit, as a small state machine with no React in it.
 *
 * The backend sends named SSE events (`id: <seq>`, `event: <type>`, `data: {seq,type,data}`) with a
 * gapless per-audit sequence, replays from the first event on a fresh connection, and resumes
 * after `Last-Event-ID` / `?last_event_id=`. Native EventSource retries by itself and sends
 * Last-Event-ID. If it gives up (readyState CLOSED) we ask whether the audit is finished (the
 * server answers 204 for a finished audit, which looks like a close) and otherwise open a new
 * connection with ?last_event_id=<last seen id> after a backoff.
 */
import { z } from "zod";

export const EVENT_TYPES = [
  "audit_started",
  "audit_resumed",
  "run_started",
  "answer_extracted",
  "run_verified",
  "run_failed",
  "source_fetched",
  "audit_completed",
  "audit_capped",
  "audit_failed",
] as const;

export const TERMINAL_EVENT_TYPES: ReadonlySet<string> = new Set(["audit_completed", "audit_capped", "audit_failed"]);

export type ConnectionState = "connecting" | "live" | "reconnecting" | "finished";

export interface StreamEvent {
  id: number | null;
  type: string;
  data: Record<string, unknown>;
}

export interface EventSourceLike {
  readonly readyState: number;
  onopen: ((ev: Event) => void) | null;
  onerror: ((ev: Event) => void) | null;
  addEventListener(type: string, listener: (ev: MessageEvent) => void): void;
  close(): void;
}

export const READY_STATE_CLOSED = 2;

const payloadSchema = z.object({
  seq: z.number().optional(),
  type: z.string().optional(),
  data: z.record(z.string(), z.unknown()).optional(),
});

export interface StreamOptions {
  /** The URL to connect to; `lastId` is null on the first connection. */
  url: (lastId: number | null) => string;
  createSource: (url: string) => EventSourceLike;
  onEvent: (event: StreamEvent) => void;
  onState: (state: ConnectionState) => void;
  /** Called when the connection was closed without a terminal event: is the audit over? */
  isFinished: () => Promise<boolean>;
  backoffMs?: (attempt: number) => number;
}

export function defaultBackoffMs(attempt: number): number {
  return Math.min(30_000, 1_000 * 2 ** attempt);
}

export function auditEventsUrl(auditId: string, lastId: number | null): string {
  const base = `/api/audits/${encodeURIComponent(auditId)}/events`;
  return lastId === null ? base : `${base}?last_event_id=${lastId}`;
}

function parseId(raw: string | undefined): number | null {
  if (raw === undefined || raw === "" || !/^\d+$/.test(raw)) return null;
  return Number(raw);
}

export class AuditEventStream {
  private source: EventSourceLike | null = null;
  private lastId: number | null = null;
  private readonly seen = new Set<number>();
  private attempt = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private stopped = false;
  private done = false;

  constructor(private readonly opts: StreamOptions) {}

  get lastEventId(): number | null {
    return this.lastId;
  }

  start(): void {
    this.open();
  }

  /** Stop for good (unmount, or the audit is over). No callback fires afterwards. */
  stop(): void {
    this.stopped = true;
    this.teardown();
  }

  private teardown(): void {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    if (this.source) {
      this.source.onopen = null;
      this.source.onerror = null;
      this.source.close();
    }
    this.source = null;
  }

  private open(): void {
    if (this.stopped || this.done) return;
    this.opts.onState(this.lastId === null && this.attempt === 0 ? "connecting" : "reconnecting");
    const src = this.opts.createSource(this.opts.url(this.lastId));
    this.source = src;
    src.onopen = () => {
      if (src !== this.source) return;
      this.attempt = 0;
      this.opts.onState("live");
    };
    src.onerror = () => this.onError(src);
    for (const type of EVENT_TYPES) src.addEventListener(type, (ev) => this.onMessage(src, type, ev));
  }

  private onMessage(src: EventSourceLike, type: string, ev: MessageEvent): void {
    if (src !== this.source || this.stopped || this.done) return;
    let data: Record<string, unknown> = {};
    let payloadSeq: number | null = null;
    try {
      const parsed = payloadSchema.safeParse(JSON.parse(String(ev.data)));
      if (parsed.success) {
        data = parsed.data.data ?? {};
        payloadSeq = parsed.data.seq ?? null;
      }
    } catch {
      // A malformed payload still has an id; hand on an empty body rather than dropping progress.
    }
    const id = parseId(ev.lastEventId) ?? payloadSeq;
    if (id !== null) {
      if (this.seen.has(id)) return; // never process the same event id twice
      this.seen.add(id);
      this.lastId = this.lastId === null ? id : Math.max(this.lastId, id);
    }
    this.opts.onEvent({ id, type, data });
    if (TERMINAL_EVENT_TYPES.has(type)) this.finish();
  }

  private finish(): void {
    this.done = true;
    this.teardown();
    this.opts.onState("finished");
  }

  private onError(src: EventSourceLike): void {
    if (src !== this.source || this.stopped || this.done) return;
    this.opts.onState("reconnecting");
    if (src.readyState !== READY_STATE_CLOSED) return; // the browser is retrying by itself
    // The browser gave up. That is also what a 204 (finished audit) looks like.
    src.close();
    this.source = null;
    void this.recover();
  }

  private async recover(): Promise<void> {
    let finished = false;
    try {
      finished = await this.opts.isFinished();
    } catch {
      finished = false; // cannot tell: keep trying
    }
    if (this.stopped || this.done) return;
    if (finished) {
      this.finish();
      return;
    }
    const delay = (this.opts.backoffMs ?? defaultBackoffMs)(this.attempt);
    this.attempt += 1;
    this.timer = setTimeout(() => {
      this.timer = null;
      this.open();
    }, delay);
  }
}

/** What to show: a finished audit is "finished" whatever the (never opened) stream says. */
export function displayedConnection(auditStatus: string | undefined, state: ConnectionState): ConnectionState {
  return auditStatus !== undefined && auditStatus !== "running" ? "finished" : state;
}
