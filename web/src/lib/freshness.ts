export interface Freshness {
  /** "3 days ago". */
  ago: string;
  /** Old enough that the live page may have changed since. */
  stale: boolean;
}

export const STALE_AFTER_DAYS = 7;

/** How old a snapshot is. An unparseable or future timestamp is reported as unknown, not as "just now". */
export function describeFreshness(iso: string | null | undefined, now: number): Freshness | null {
  if (!iso) return null;
  const then = Date.parse(iso);
  if (!Number.isFinite(then)) return null;
  const seconds = Math.floor((now - then) / 1000);
  if (seconds < -60) return null;
  const s = Math.max(0, seconds);
  const unit = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"} ago`;
  let ago: string;
  if (s < 60) ago = "just now";
  else if (s < 3600) ago = unit(Math.floor(s / 60), "minute");
  else if (s < 86_400) ago = unit(Math.floor(s / 3600), "hour");
  else ago = unit(Math.floor(s / 86_400), "day");
  return { ago, stale: s >= STALE_AFTER_DAYS * 86_400 };
}
