/**
 * The review outbox: how a review decision becomes a POST without losing it, duplicating it, or
 * making the undo window a lie.
 *
 * - Nothing is POSTed until UNDO_MS (5 s) after the click. In that window `undo` removes the entry
 *   and nothing was ever sent.
 * - Every entry is persisted to localStorage, so a reload keeps it. An entry that comes due while
 *   the page is closed is sent on the next load.
 * - On `pagehide` the entries still waiting are flushed with `keepalive`, because the user can no
 *   longer undo once they leave.
 * - The review endpoint has no Idempotency-Key, so duplicates are prevented on this side. An entry
 *   is marked "sending" (with the tab that owns it) before the POST. If the page dies mid-send, or
 *   a send failed in a way that may still have reached the server, the next attempt first asks the
 *   server (`verify`) whether the review is already there, and only sends when it is not. Another
 *   tab's fresh send is left alone; its lease expires after LEASE_MS.
 * - Reviews are append-only on the server and the latest wins, so a newer decision for the same run
 *   replaces one that is still waiting, and runs in order after one that is already sending.
 *
 * Plain TypeScript with injected dependencies, so it is tested with fake timers and storage.
 */
import { z } from "zod";
import type { ReviewRequest } from "@/lib/review-draft";

export const UNDO_MS = 5000;
export const STORAGE_KEY = "review-outbox:v1";
/** A "sending" mark older than this is treated as a dead send and re-checked. */
export const LEASE_MS = 30_000;
/** Another tab only adopts a waiting entry this long after it was due and its owner did not send it. */
export const ADOPT_GRACE_MS = 10_000;

const entrySchema = z.object({
  id: z.string(),
  runId: z.string(),
  label: z.string(),
  request: z.object({
    action: z.enum(["approve", "correct", "reject"]),
    reason: z.string(),
    corrected_value: z.string().optional(),
  }),
  createdAt: z.number(),
  dueAt: z.number(),
  status: z.enum(["waiting", "sending", "failed"]),
  ownerTab: z.string(),
  sendingAt: z.number().nullable(),
  sendingBy: z.string().nullable(),
  attempts: z.number(),
  verifyFirst: z.boolean(),
  error: z.string().nullable(),
  retryable: z.boolean(),
});

export type PendingReview = z.infer<typeof entrySchema>;

export type SendResult = { kind: "ok" } | { kind: "rejected"; message: string } | { kind: "retry"; message: string };
export type VerifyResult = "delivered" | "not_delivered" | "unknown";

export interface OutboxDeps {
  storage: Pick<Storage, "getItem" | "setItem"> | null;
  now: () => number;
  newId: () => string;
  tabId: string;
  send: (entry: PendingReview, options: { keepalive: boolean }) => Promise<SendResult>;
  verify: (entry: PendingReview) => Promise<VerifyResult>;
  /** Called after an entry left the outbox because the server has the review. */
  onSettled?: (entry: PendingReview) => void;
}

export interface Notice {
  seq: number;
  text: string;
}

export interface OutboxSnapshot {
  entries: readonly PendingReview[];
  /** The latest thing worth announcing to a screen reader. */
  notice: Notice | null;
}

export interface EnqueueInput {
  runId: string;
  label: string;
  request: ReviewRequest;
}

export function parseEntries(raw: string | null): PendingReview[] {
  if (raw === null) return [];
  try {
    const parsed = z.array(entrySchema).safeParse(JSON.parse(raw));
    return parsed.success ? parsed.data : [];
  } catch {
    return [];
  }
}

const VERB: Record<ReviewRequest["action"], string> = { approve: "Approval", correct: "Correction", reject: "Rejection" };

export class ReviewOutbox {
  private entries: PendingReview[] = [];
  private snapshot: OutboxSnapshot = { entries: [], notice: null };
  private notice: Notice | null = null;
  private listeners = new Set<() => void>();
  private inFlight = new Set<string>();
  private timer: ReturnType<typeof setTimeout> | null = null;
  private stopFns: Array<() => void> = [];
  private started = false;

  constructor(private readonly deps: OutboxDeps) {}

  // ---------------------------------------------------------------- store api (useSyncExternalStore)

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getSnapshot = (): OutboxSnapshot => this.snapshot;

  // ---------------------------------------------------------------- lifecycle

  start(): void {
    if (this.started) return;
    this.started = true;
    this.reload();
    this.recover();
    this.emit();
    this.schedule();
    if (typeof window !== "undefined") {
      const onStorage = (event: StorageEvent) => {
        if (event.key !== null && event.key !== STORAGE_KEY) return;
        this.reload();
        this.emit();
        this.schedule();
      };
      const onPageHide = () => this.flushForUnload();
      window.addEventListener("storage", onStorage);
      window.addEventListener("pagehide", onPageHide);
      this.stopFns.push(
        () => window.removeEventListener("storage", onStorage),
        () => window.removeEventListener("pagehide", onPageHide),
      );
    }
  }

  stop(): void {
    this.started = false;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    for (const fn of this.stopFns) fn();
    this.stopFns = [];
  }

  // ---------------------------------------------------------------- user actions

  enqueue(input: EnqueueInput): string {
    this.reload();
    const now = this.deps.now();
    const id = this.deps.newId();
    // Latest wins: a decision still inside its undo window (or failed) is superseded by a new one.
    this.entries = this.entries.filter((e) => !(e.runId === input.runId && e.status !== "sending"));
    this.entries.push({
      id,
      runId: input.runId,
      label: input.label,
      request: input.request,
      createdAt: now,
      dueAt: now + UNDO_MS,
      status: "waiting",
      ownerTab: this.deps.tabId,
      sendingAt: null,
      sendingBy: null,
      attempts: 0,
      verifyFirst: false,
      error: null,
      retryable: true,
    });
    this.say(`${VERB[input.request.action]} queued. You can undo it for ${UNDO_MS / 1000} seconds.`);
    this.commit();
    return id;
  }

  /** True when the entry was still inside its window and is now gone, having never been sent. */
  undo(id: string): boolean {
    this.reload();
    const e = this.entries.find((x) => x.id === id);
    if (!e || e.status !== "waiting" || this.deps.now() >= e.dueAt) return false;
    this.entries = this.entries.filter((x) => x.id !== id);
    this.say("Undone. Nothing was sent.");
    this.commit();
    return true;
  }

  retry(id: string): void {
    this.reload();
    this.patch(id, (e) => (e.status === "failed" && e.retryable ? { status: "waiting", dueAt: this.deps.now(), ownerTab: this.deps.tabId, error: null } : {}));
    this.commit();
  }

  discard(id: string): void {
    this.reload();
    const e = this.entries.find((x) => x.id === id);
    if (!e || e.status === "sending") return;
    this.entries = this.entries.filter((x) => x.id !== id);
    this.say("Review discarded.");
    this.commit();
  }

  // ---------------------------------------------------------------- sending

  private isBlocked(e: PendingReview): boolean {
    return this.entries.some((o) => o.id !== e.id && o.runId === e.runId && o.createdAt < e.createdAt && o.status !== "failed");
  }

  private isSendable(e: PendingReview, now: number): boolean {
    if (e.status !== "waiting" || e.dueAt > now || this.inFlight.has(e.id) || this.isBlocked(e)) return false;
    return e.ownerTab === this.deps.tabId || now >= e.dueAt + ADOPT_GRACE_MS;
  }

  private tick = (): void => {
    this.timer = null;
    this.reload();
    this.recover();
    const now = this.deps.now();
    for (const e of [...this.entries]) if (this.isSendable(e, now)) void this.dispatch(e.id, false);
    this.schedule();
  };

  private async dispatch(id: string, keepalive: boolean): Promise<void> {
    const found = this.entries.find((e) => e.id === id);
    if (!found || this.inFlight.has(id)) return;
    this.inFlight.add(id);
    this.patch(id, (e) => ({ status: "sending", sendingAt: this.deps.now(), sendingBy: this.deps.tabId, attempts: e.attempts + 1 }));
    this.commit(false);
    const entry = this.entries.find((e) => e.id === id)!;
    try {
      if (entry.verifyFirst) {
        const v = await this.deps.verify(entry);
        if (v === "delivered") {
          this.settle(id, "Review saved.");
          this.deps.onSettled?.(entry);
          return;
        }
      }
      const result = await this.deps.send(entry, { keepalive });
      if (result.kind === "ok") {
        this.settle(id, "Review saved.");
        this.deps.onSettled?.(entry);
      } else {
        this.fail(id, result.message, result.kind === "retry");
      }
    } catch {
      // A throw means we do not know if it arrived: keep it, and verify before any resend.
      this.fail(id, "Could not reach the server.", true);
    } finally {
      this.inFlight.delete(id);
      this.schedule();
    }
  }

  private settle(id: string, text: string): void {
    this.reload();
    this.entries = this.entries.filter((e) => e.id !== id);
    this.say(text);
    this.commit(false);
  }

  private fail(id: string, message: string, retryable: boolean): void {
    this.reload();
    this.patch(id, () => ({ status: "failed", error: message, retryable, verifyFirst: retryable, sendingAt: null, sendingBy: null }));
    this.say(`A review could not be saved: ${message}`);
    this.commit(false);
  }

  /** The user is leaving: nothing can be undone any more, so send what is waiting. */
  private flushForUnload(): void {
    this.reload();
    for (const e of [...this.entries]) {
      if (e.status === "waiting" && e.ownerTab === this.deps.tabId && !e.verifyFirst && !this.inFlight.has(e.id) && !this.isBlocked(e)) {
        void this.dispatch(e.id, true);
      }
    }
  }

  /** A "sending" entry nobody is working on (page reloaded, other tab died) must be checked, not trusted. */
  private recover(): void {
    const now = this.deps.now();
    let changed = false;
    this.entries = this.entries.map((e) => {
      if (e.status !== "sending" || this.inFlight.has(e.id)) return e;
      const mine = e.sendingBy === this.deps.tabId;
      const expired = e.sendingAt === null || now - e.sendingAt > LEASE_MS;
      if (!mine && !expired) return e;
      changed = true;
      return { ...e, status: "waiting", verifyFirst: true, dueAt: Math.min(e.dueAt, now), ownerTab: this.deps.tabId, sendingAt: null, sendingBy: null };
    });
    if (changed) this.persist();
  }

  private schedule(): void {
    if (!this.started) return;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    const now = this.deps.now();
    let next: number | null = null;
    const consider = (t: number) => {
      next = next === null ? t : Math.min(next, t);
    };
    for (const e of this.entries) {
      if (this.inFlight.has(e.id)) continue;
      if (e.status === "waiting" && !this.isBlocked(e)) consider(e.ownerTab === this.deps.tabId ? e.dueAt : e.dueAt + ADOPT_GRACE_MS);
      else if (e.status === "sending" && e.sendingAt !== null) consider(e.sendingAt + LEASE_MS + 1);
    }
    if (next === null) return;
    this.timer = setTimeout(this.tick, Math.max(0, next - now));
  }

  // ---------------------------------------------------------------- storage

  private patch(id: string, fn: (e: PendingReview) => Partial<PendingReview>): void {
    this.entries = this.entries.map((e) => (e.id === id ? { ...e, ...fn(e) } : e));
  }

  private reload(): void {
    const { storage } = this.deps;
    if (!storage) return;
    try {
      this.entries = parseEntries(storage.getItem(STORAGE_KEY));
    } catch {
      // storage unavailable: keep working from memory
    }
  }

  private persist(): void {
    try {
      this.deps.storage?.setItem(STORAGE_KEY, JSON.stringify(this.entries));
    } catch {
      // quota or private mode: the in-memory queue still works for this page
    }
  }

  private say(text: string): void {
    this.notice = { seq: (this.notice?.seq ?? 0) + 1, text };
  }

  private emit(): void {
    this.snapshot = { entries: [...this.entries], notice: this.notice };
    for (const l of this.listeners) l();
  }

  private commit(reschedule = true): void {
    this.persist();
    this.emit();
    if (reschedule) this.schedule();
  }
}
