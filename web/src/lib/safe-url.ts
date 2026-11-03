/**
 * Citation and source URLs come from model output and fetched pages: untrusted. They are shown as
 * text, and only made clickable when they are plain http(s) URLs (never javascript:, data:, ...).
 */
export function safeHref(url: string): string | null {
  try {
    const u = new URL(url);
    return u.protocol === "http:" || u.protocol === "https:" ? u.href : null;
  } catch {
    return null;
  }
}
