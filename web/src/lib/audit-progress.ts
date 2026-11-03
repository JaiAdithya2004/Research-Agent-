import type { StreamEvent } from "@/lib/audit-events";

/**
 * Progress counted from the event stream. A run is "finished" when it was verified or failed.
 * The same run can emit several events and the stream is replayed on reconnect, so runs are
 * counted by identity, never by number of events.
 */
export interface ProgressState {
  planned: number | null;
  finished: ReadonlySet<string>;
  failed: ReadonlySet<string>;
  sourcesFetched: number;
}

export const INITIAL_PROGRESS: ProgressState = {
  planned: null,
  finished: new Set(),
  failed: new Set(),
  sourcesFetched: 0,
};

function stepKey(data: Record<string, unknown>): string | null {
  const step = data.step;
  if (typeof step !== "object" || step === null) return null;
  const s = step as Record<string, unknown>;
  return [s.question_idx, s.provider, s.model, s.repeat_idx, s.search_mode].map(String).join("|");
}

export function reduceProgress(state: ProgressState, event: StreamEvent): ProgressState {
  switch (event.type) {
    case "audit_started":
    case "audit_resumed": {
      const planned = event.data.planned_steps;
      return typeof planned === "number" ? { ...state, planned } : state;
    }
    case "run_verified":
    case "run_failed": {
      const key = stepKey(event.data);
      if (key === null || state.finished.has(key)) return state;
      const finished = new Set(state.finished).add(key);
      const failed = event.type === "run_failed" ? new Set(state.failed).add(key) : state.failed;
      return { ...state, finished, failed };
    }
    case "source_fetched":
      return { ...state, sourcesFetched: state.sourcesFetched + 1 };
    default:
      return state;
  }
}

export interface ProgressView {
  /** Total planned runs, or null when not known yet. */
  planned: number | null;
  done: number;
  failed: number;
  /** 0..1, or null when the total is unknown. */
  fraction: number | null;
  text: string;
}

export function progressText(done: number, planned: number | null): string {
  return planned === null ? `${done} runs finished` : `${done} of ${planned} runs finished`;
}

export function progressView(state: ProgressState, runsFinishedFromRows = 0): ProgressView {
  // The run rows (fetched with the audit) can be ahead of the stream right after a reload.
  const done = Math.max(state.finished.size, runsFinishedFromRows);
  const planned = state.planned === null ? null : Math.max(state.planned, done);
  return {
    planned,
    done,
    failed: state.failed.size,
    fraction: planned === null || planned === 0 ? null : Math.min(1, done / planned),
    text: progressText(done, planned),
  };
}

/**
 * Text for the aria-live region. It only changes at about every tenth of the run (and at the
 * end), so a screen reader hears a handful of updates, not one per event.
 */
export function announcementText(done: number, planned: number | null): string {
  if (planned === null) return done === 0 ? "" : progressText(done, null);
  if (done >= planned) return progressText(planned, planned);
  const step = Math.max(1, Math.ceil(planned / 10));
  return progressText(Math.floor(done / step) * step, planned);
}
