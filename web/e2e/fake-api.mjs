/**
 * A tiny fake of the audit API for the Playwright tests. Plain node http, no dependencies.
 *
 * The Next server is pointed at it with API_BASE_URL, so the real BFF proxy, the real browser
 * code and the real generated client run; only the backend is replaced. It serves the shapes in
 * ../openapi.json from a state object that each test installs through the control endpoints:
 *
 *   POST /__control/reset   body = state (see below)         resets everything, including the log
 *   GET  /__control/log                                      every API request seen, in order
 *   POST /__control/emit    {audit_id, count}                releases the next `count` SSE events
 *   POST /__control/drop    {audit_id}                       destroys the open SSE connections
 *
 * state = {
 *   audits: AuditOut[], auditList: AuditListItem[],
 *   runs: {[auditId]: RunSummaryOut[]}, runDetails: {[runId]: RunDetailOut},
 *   queue: QueueItem[], sources: {[auditId]: SourceOut[]},
 *   sourceDetails: {[auditId]: {[sourceId]: SourceDetailOut}}, gaps: {[auditId]: GapReportOut},
 *   evals: EvalRunOut[],
 *   streams: {[auditId]: {events: {type, data}[], finalAudit?: AuditOut, finalRuns?: RunSummaryOut[]}},
 *   createResponse: AuditOut | null    what POST /audits creates (status 202)
 * }
 *
 * Every other path needs the right X-API-Key (FAKE_API_KEY), like the real API: that is how the
 * tests prove the proxy adds the key. SSE follows the real wire format: `id`, `event`, `data` with
 * {seq, type, data}, replay after Last-Event-ID / ?last_event_id=, and 204 for a finished audit.
 */
import http from "node:http";

const PORT = Number(process.env.FAKE_API_PORT ?? 4010);
const API_KEY = process.env.FAKE_API_KEY ?? "";
const TERMINAL = new Set(["audit_completed", "audit_capped", "audit_failed"]);

const emptyState = () => ({
  audits: [],
  auditList: [],
  runs: {},
  runDetails: {},
  queue: [],
  sources: {},
  sourceDetails: {},
  gaps: {},
  evals: [],
  streams: {},
  createResponse: null,
});

let state = emptyState();
let log = [];
let released = {}; // audit id -> number of events released
let connections = []; // open SSE connections {auditId, res, lastSent}
let idempotency = new Map(); // Idempotency-Key -> audit id

const send = (res, status, body, headers = {}) => {
  res.writeHead(status, { "content-type": "application/json", ...headers });
  res.end(JSON.stringify(body));
};

const readBody = (req) =>
  new Promise((resolve) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
  });

function eventsOf(auditId) {
  return (state.streams[auditId]?.events ?? []).map((e, i) => ({ seq: i + 1, type: e.type, data: e.data ?? {} }));
}

function writeEvent(res, e) {
  res.write(`id: ${e.seq}\nevent: ${e.type}\ndata: ${JSON.stringify({ seq: e.seq, type: e.type, data: e.data })}\n\n`);
}

function flush(conn) {
  const limit = released[conn.auditId] ?? 0;
  for (const e of eventsOf(conn.auditId)) {
    if (e.seq > conn.lastSent && e.seq <= limit) {
      writeEvent(conn.res, e);
      conn.lastSent = e.seq;
    }
  }
}

function applyTerminal(auditId) {
  const stream = state.streams[auditId];
  if (!stream) return;
  if (stream.finalAudit) state.audits = state.audits.map((a) => (a.id === auditId ? stream.finalAudit : a));
  if (stream.finalRuns) state.runs[auditId] = stream.finalRuns;
}

function emit(auditId, count) {
  const events = eventsOf(auditId);
  const before = released[auditId] ?? 0;
  const after = Math.min(events.length, before + count);
  released[auditId] = after;
  // The audit row changes before the terminal event is written, so the refetch it triggers sees the end state.
  if (events.slice(before, after).some((e) => TERMINAL.has(e.type))) applyTerminal(auditId);
  for (const conn of connections.filter((c) => c.auditId === auditId)) flush(conn);
}

function handleControl(req, res, url, raw) {
  const body = raw ? JSON.parse(raw) : {};
  if (url.pathname === "/__control/reset" && req.method === "POST") {
    for (const c of connections) c.res.destroy();
    state = { ...emptyState(), ...body };
    log = [];
    released = {};
    connections = [];
    idempotency = new Map();
    return send(res, 200, { ok: true });
  }
  if (url.pathname === "/__control/log") return send(res, 200, log);
  if (url.pathname === "/__control/emit" && req.method === "POST") {
    emit(body.audit_id, body.count ?? 1);
    return send(res, 200, { released: released[body.audit_id] ?? 0 });
  }
  if (url.pathname === "/__control/drop" && req.method === "POST") {
    const mine = connections.filter((c) => c.auditId === body.audit_id);
    for (const c of mine) c.res.destroy();
    connections = connections.filter((c) => !mine.includes(c));
    return send(res, 200, { dropped: mine.length });
  }
  return send(res, 404, { detail: "unknown control endpoint" });
}

function paginate(items, url) {
  const limit = Number(url.searchParams.get("limit") ?? 20);
  return { items: items.slice(0, limit), next_cursor: null };
}

function handleEvents(req, res, url, auditId) {
  const audit = state.audits.find((a) => a.id === auditId);
  if (!audit) return send(res, 404, { detail: "audit not found" });
  if (audit.status !== "running") {
    res.writeHead(204);
    return res.end();
  }
  const header = req.headers["last-event-id"];
  const raw = (Array.isArray(header) ? header[0] : header) ?? url.searchParams.get("last_event_id");
  const lastSent = raw && /^\d+$/.test(raw) ? Number(raw) : 0;
  res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache", connection: "keep-alive" });
  res.write("retry: 300\n\n");
  const conn = { auditId, res, lastSent };
  connections.push(conn);
  req.on("close", () => {
    connections = connections.filter((c) => c !== conn);
  });
  flush(conn);
}

function handleReview(res, runId, raw) {
  const detail = state.runDetails[runId];
  if (!detail) return send(res, 404, { detail: "run not found" });
  const body = JSON.parse(raw || "{}");
  const review = {
    action: body.action,
    corrected_value: body.corrected_value ?? null,
    reason: body.reason,
    reviewer: "e2e-reviewer",
    timestamp: new Date().toISOString(),
  };
  state.runDetails[runId] = { ...detail, review };
  state.queue = state.queue.filter((q) => q.run_id !== runId);
  return send(res, 201, { id: `rev-${log.length}`, run_id: runId, audit_id: detail.audit_id, ...review, summary: null });
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", `http://127.0.0.1:${PORT}`);
  const raw = req.method === "POST" ? await readBody(req) : "";
  if (url.pathname.startsWith("/__control/")) return handleControl(req, res, url, raw);

  const headers = {
    "idempotency-key": req.headers["idempotency-key"] ?? null,
    "last-event-id": req.headers["last-event-id"] ?? null,
    "x-api-key-ok": req.headers["x-api-key"] === API_KEY && API_KEY !== "",
    cookie: req.headers.cookie ?? null,
  };
  let parsed = null;
  try {
    parsed = raw ? JSON.parse(raw) : null;
  } catch {
    parsed = raw;
  }
  log.push({ method: req.method, path: url.pathname, query: Object.fromEntries(url.searchParams), headers, body: parsed });

  if (url.pathname === "/health") return send(res, 200, { status: "ok" });
  if (!headers["x-api-key-ok"]) return send(res, 401, { detail: "invalid API key" });

  const parts = url.pathname.split("/").filter(Boolean).map(decodeURIComponent);
  const [a, b, c, d] = parts;

  if (req.method === "POST" && a === "audits" && parts.length === 1) {
    const key = req.headers["idempotency-key"];
    if (!key) return send(res, 422, { detail: "Idempotency-Key header is required" });
    const existing = idempotency.get(key);
    if (existing) return send(res, 200, state.audits.find((x) => x.id === existing));
    const created = state.createResponse;
    if (!created) return send(res, 503, { detail: "no createResponse configured" });
    idempotency.set(key, created.id);
    state.audits = [...state.audits.filter((x) => x.id !== created.id), created];
    return send(res, 202, created);
  }
  if (req.method === "POST" && a === "mentions" && c === "review") return handleReview(res, b, raw);
  if (req.method !== "GET") return send(res, 405, { detail: "method not allowed" });

  if (a === "audits" && parts.length === 1) return send(res, 200, paginate(state.auditList, url));
  if (a === "audits" && parts.length === 2) {
    const audit = state.audits.find((x) => x.id === b);
    return audit ? send(res, 200, audit) : send(res, 404, { detail: "audit not found" });
  }
  if (a === "audits" && c === "events" && parts.length === 3) return handleEvents(req, res, url, b);
  if (a === "audits" && c === "runs" && parts.length === 3) return send(res, 200, state.runs[b] ?? []);
  if (a === "audits" && c === "gaps" && parts.length === 3) {
    return state.gaps[b] ? send(res, 200, state.gaps[b]) : send(res, 404, { detail: "audit not found" });
  }
  if (a === "audits" && c === "sources" && parts.length === 3) {
    let rows = state.sources[b] ?? [];
    for (const f of ["source_type", "fetch_status", "domain"]) {
      const v = url.searchParams.get(f);
      if (v) rows = rows.filter((r) => String(r[f]).includes(v));
    }
    return send(res, 200, rows);
  }
  if (a === "audits" && c === "sources" && parts.length === 4) {
    const detail = state.sourceDetails[b]?.[d];
    return detail ? send(res, 200, detail) : send(res, 404, { detail: "source not found" });
  }
  if (a === "runs" && parts.length === 2) {
    const detail = state.runDetails[b];
    return detail ? send(res, 200, detail) : send(res, 404, { detail: "run not found" });
  }
  if (a === "review" && b === "queue") {
    const auditId = url.searchParams.get("audit_id");
    return send(res, 200, paginate(state.queue.filter((q) => !auditId || q.audit_id === auditId), url));
  }
  if (a === "evals" && b === "runs") return send(res, 200, state.evals);
  return send(res, 404, { detail: "not found" });
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`fake audit API on http://127.0.0.1:${PORT}`);
});
