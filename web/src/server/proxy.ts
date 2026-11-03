/**
 * Backend-for-frontend proxy core. The browser talks to /api/*, this adds the server-only API key
 * and forwards to the FastAPI backend. Kept free of Next.js types so it can be unit tested.
 */

export interface ProxyConfig {
  baseUrl: string | undefined;
  apiKey: string | undefined;
}

/** Request headers that are forwarded. Everything else (cookies, authorization, ...) is dropped. */
const FORWARD_REQUEST_HEADERS = ["accept", "content-type", "idempotency-key", "last-event-id"];

/** Response headers that are passed back. set-cookie and hop-by-hop headers are not. */
const FORWARD_RESPONSE_HEADERS = [
  "content-type",
  "retry-after",
  "cache-control",
  "etag",
  "last-modified",
  "location",
  "x-request-id",
];

const ALLOWED_METHODS = new Set(["GET", "POST"]);

function json(status: number, detail: string): Response {
  return new Response(JSON.stringify({ detail }), {
    status,
    headers: { "content-type": "application/json" },
  });
}

export function configFromEnv(env: Record<string, string | undefined> = process.env): ProxyConfig {
  return { baseUrl: env.API_BASE_URL || "http://127.0.0.1:8000", apiKey: env.AUDIT_API_KEY };
}

export function buildTarget(baseUrl: string, segments: string[], search: string): string | null {
  if (segments.length === 0) return null;
  for (const s of segments) {
    if (s === "" || s === "." || s === ".." || s.includes("/") || s.includes("\\")) return null;
  }
  const base = baseUrl.replace(/\/+$/, "");
  return `${base}/${segments.map(encodeURIComponent).join("/")}${search}`;
}

export async function proxyRequest(
  request: Request,
  segments: string[],
  config: ProxyConfig = configFromEnv(),
  fetchImpl: typeof fetch = fetch,
): Promise<Response> {
  if (!ALLOWED_METHODS.has(request.method)) return json(405, "method not allowed");
  if (!config.baseUrl || !config.apiKey) {
    return json(500, "the API proxy is not configured (set API_BASE_URL and AUDIT_API_KEY on the server)");
  }
  const search = new URL(request.url).search;
  const target = buildTarget(config.baseUrl, segments, search);
  if (target === null) return json(400, "invalid path");

  const headers = new Headers();
  for (const name of FORWARD_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value !== null) headers.set(name, value);
  }
  headers.set("x-api-key", config.apiKey);

  const hasBody = request.method !== "GET";
  let upstream: Response;
  try {
    upstream = await fetchImpl(target, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      redirect: "manual", // never follow a redirect with the key attached
      signal: request.signal,
      cache: "no-store",
    });
  } catch {
    // Do not echo the error: it can contain the internal base URL.
    return json(502, "the audit API could not be reached");
  }

  const out = new Headers();
  for (const name of FORWARD_RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value !== null) out.set(name, value);
  }
  const isStream = (upstream.headers.get("content-type") ?? "").startsWith("text/event-stream");
  if (isStream) {
    out.set("cache-control", "no-cache, no-transform");
    out.set("x-accel-buffering", "no");
  }
  const noBody = upstream.status === 204 || upstream.status === 205 || upstream.status === 304;
  // upstream.body is passed through as a stream: chunks are forwarded as they arrive.
  return new Response(noBody ? null : upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: out,
  });
}
