/** Reading the API's error bodies. FastAPI sends `detail` as a string, or a list for validation. */

export type FieldErrors = Record<string, string>;

export class ApiError extends Error {
  readonly status: number;
  readonly retryAfterSeconds: number | null;

  constructor(status: number, message: string, retryAfterSeconds: number | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

interface DetailItem {
  loc?: unknown;
  msg?: unknown;
}

function detailOf(body: unknown): unknown {
  return typeof body === "object" && body !== null && "detail" in body ? (body as { detail: unknown }).detail : undefined;
}

/** One readable sentence for any error body. */
export function messageFromBody(body: unknown, fallback: string): string {
  const detail = detailOf(body);
  if (typeof detail === "string" && detail.trim() !== "") return detail;
  if (Array.isArray(detail)) {
    const parts = (detail as DetailItem[])
      .map((d) => (typeof d.msg === "string" ? d.msg : null))
      .filter((m): m is string => m !== null);
    if (parts.length > 0) return parts.join(" ");
  }
  return fallback;
}

/** Validation errors by dotted field path ("questions.2"). Empty when the body has no locations. */
export function fieldErrorsFromBody(body: unknown): FieldErrors {
  const detail = detailOf(body);
  const out: FieldErrors = {};
  if (!Array.isArray(detail)) return out;
  for (const item of detail as DetailItem[]) {
    if (!Array.isArray(item.loc) || typeof item.msg !== "string") continue;
    const loc = (item.loc as unknown[]).filter((p): p is string | number => typeof p === "string" || typeof p === "number");
    const path = (loc[0] === "body" ? loc.slice(1) : loc).join(".");
    if (path !== "" && !(path in out)) out[path] = item.msg;
  }
  return out;
}

export function retryAfterSeconds(headers: Headers): number | null {
  const raw = headers.get("retry-after");
  if (raw === null) return null;
  const n = Number(raw);
  return Number.isFinite(n) && n >= 0 ? Math.ceil(n) : null;
}

/** For query functions: turn an openapi-fetch result into data or a thrown ApiError. */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }, what: string): T {
  if (result.data !== undefined && result.response.ok) return result.data;
  const status = result.response.status;
  const fallback =
    status === 404
      ? `${what} was not found.`
      : status === 401
        ? "The server rejected the API key. Check AUDIT_API_KEY on the web server."
        : status === 502 || status === 503
          ? "The audit API is unavailable right now."
          : `Could not load ${what.toLowerCase()} (HTTP ${status}).`;
  throw new ApiError(status, messageFromBody(result.error, fallback), retryAfterSeconds(result.response.headers));
}
