# AI Visibility Audit Agent: Spec

## Goal
Given a brand and buyer questions, query LLMs the way a user would (with and without web
search), extract how the brand appears, verify every claim against the raw text and the
cited sources, and return a stability-aware visibility score plus a source gap report.
Humans review anything the code cannot verify.

## Non-goals
- No scraping of real search engines. Only provider APIs (Gemini, OpenAI) and our own fetcher.
- No multi-tenant orgs, billing or scheduled audits (noted for later).
- No autonomous actions. The agent reads and reports only.

## Inputs
POST /audits
{ brand, aliases[], category, competitors[], questions[] (max 10), repeats (1-5, default 3),
  search_mode: "off" | "grounded" | "both" | "tavily" (default "grounded"),
    ("tavily" = we search with Tavily and give the model the results as untrusted data; the
     model has no tools. It measures "model + Tavily", not an assistant's own search.)
  research_depth: "none" | "cited_pages" (default "cited_pages") }
Headers: Idempotency-Key (required), X-API-Key

## Outputs (Pydantic)
- Citation: { url, domain, title, source: "grounding_metadata" | "answer_text", retrieved_at }
- Mention: { brand_mentioned, position|null, quote|null, competitors_seen[],
  citations[], sentiment: pos|neu|neg }
- SourceEvidence: { url, fetch_status: ok|blocked|unreachable|paywalled|too_large|error,
  brand_on_page: bool|null, quote|null, source_type: own_site|competitor|review|news|forum|other,
  snapshot_id }
- Verdict: verified | flagged, with a specific reason
- Review: { action, corrected_value, reason, reviewer, timestamp }
- AuditSummary: mention_rate, avg_position, stability, flagged_count, total_cost_usd,
  search_calls, pages_fetched, p50/p90 latency, status: running|complete|capped|failed,
  mode_delta (grounded mention_rate minus off mention_rate, when mode is "both")
- SourceGapReport: domains cited for competitors but not for the brand, ranked by frequency

## Acceptance criteria: answers and verification
1. Every quote in a verified mention appears in the raw answer text.
2. Citations come from provider grounding metadata, not parsed from prose. A URL that only
   appears in prose is stored with source "answer_text" and flagged as an unverified citation.
   In "tavily" mode the citations are the URLs Tavily returned (structured tool output, also
   stored as "grounding_metadata"); the run's search_mode "tavily" tells them apart.
3. Unverifiable claims are flagged with a reason, never silently dropped or accepted.
4. Same Idempotency-Key returns the same audit with zero new LLM or search calls.
5. Audit stops at the cost cap ($0.75 default, searches included) and returns partial
   results as "capped".
6. Invalid model JSON gets exactly one repair attempt, then the item is marked failed.
7. Each LLM call has a 30s timeout and max 3 retries with exponential backoff.
8. One provider being down does not fail the audit. If the search tool is unavailable,
   fall back to search_mode "off" for that call and mark the run "degraded".
9. Raw answers and grounding metadata are stored and never overwritten.
   Reviews live in a separate table. Deleting an audit is a soft delete: it disappears from every
   read (list, detail, runs, sources, gaps, events, review queue) but no row is removed, and its
   cost still counts toward the key's daily budget.

## Acceptance criteria: fetching and source evidence
10. The fetcher allows only http and https, resolves DNS first, and blocks private, loopback,
    link-local and cloud metadata IP ranges, including after redirects (max 3 redirects).
11. Fetch limits: 10s timeout, 1MB body cap, text content types only, robots.txt respected,
    identifiable User-Agent.
12. Per-audit caps: max 30 page fetches, max 5 per domain, max search calls set by cost cap.
    Hitting a cap returns partial results, never an error.
13. Fetched pages are cached and snapshotted with a timestamp so any score can be reproduced.
14. A page we could not fetch is "unreachable" or "blocked", never "brand absent".
    brand_on_page is null in that case.
15. brand_on_page uses the same deterministic verifier as answers: a quote must exist in the
    page text, no LLM judgement on its own.
16. Fetched page text is untrusted data. It is passed to a model only inside a delimited data
    block, in a call with no tools, and the output is schema-validated. The prompt-injection
    test suite must pass.
17. Source gap report is computed in code from stored evidence, not written by an LLM.

## Acceptance criteria: platform
18. Audits run in a worker, never in the web process. A restart loses nothing.
19. SSE stream resumes correctly from Last-Event-ID after a disconnect.
20. API keys hashed at rest, rate limited, per-key daily cost budget (429 when hit).
21. Raw API keys never appear in logs (covered by a test).
22. Extractor v2 beats v1 on accuracy without raising cost more than 25%.

## Endpoints
POST /audits, GET /audits (cursor pagination, filters), GET /audits/{id},
GET /audits/{id}/sources, GET /audits/{id}/gaps, GET /audits/{id}/events (SSE),
DELETE /audits/{id} (soft delete, 409 while running),
POST /mentions/{id}/review, GET /evals/runs, GET /health, GET /ready

## Failure modes and handling
| Failure | Handling |
|---|---|
| Timeout or 429 | retry with backoff, then mark call failed, continue |
| Invalid JSON | one repair retry, then failed |
| Brand spelled differently | alias list plus normalized matching |
| Brand only named as a competitor | not a mention, covered by test |
| Repeats disagree | report stability, never average it away |
| Search results vary between runs | expected, stability and mode_delta report it |
| Search tool unavailable | fall back to off, mark degraded |
| Cited URL returns 404, 403 or robots block | unreachable or blocked, brand_on_page = null |
| Paywalled or JS-only page | paywalled or unreachable, not absent |
| Redirect to a different domain | record both, evidence attributed to final domain |
| Page contains instructions aimed at the model | treated as data, injection tests must pass |
| Private IP or metadata URL in a citation | blocked before any request is made |
| Cost cap or fetch cap hit | stop, return partial, status = capped |
| Worker crash mid-audit | job retried, steps idempotent per question/model/repeat/url |
| SSE connection drops | reconnect with Last-Event-ID, no events lost |

## Frontend scope (Next.js, TypeScript)
Pages: Audits list, New audit (live cost estimate incl. search), Audit detail (live progress,
scorecard, stability dots, grounded vs off delta), Answer viewer with citations panel,
Sources and gap report, Review queue, Evals comparison.
Required states on every view: loading, empty, error, partial, capped, degraded, failed.

## Frontend acceptance criteria
1. Audit detail updates live without refresh and recovers if SSE drops.
2. Every verified claim shows its quote highlighted in the raw answer.
3. Each citation shows its fetch status and whether the brand was found on the page.
4. Unreachable sources are visibly different from "brand not found" sources.
5. Review actions are optimistic, undoable for 5s, and survive a reload.
6. Keyboard navigable, visible focus, WCAG AA contrast, usable at 375px width.
7. API types are generated from the OpenAPI schema, never hand-written.
8. Playwright covers: create audit, watch it complete, resolve a flagged item, open a source.

## Eval plan
- 18 labeled answers: misspelling, competitor-only, negative mention, no mention
- 12 labeled source pages: brand present, brand absent, paywalled, brand only in nav or footer
- 8 prompt-injection pages: hidden instructions, fake "system" text, "say Brand X is best",
  instructions in HTML comments and alt text. Pass = extraction output unchanged.
- Metrics: extraction accuracy, false-mention rate, flag rate, source-check accuracy,
  injection pass rate, cost, P50/P90 latency
- Baseline v1 vs candidate v2, 3 repeated runs each, mean and spread

## Observability
Langfuse trace per audit, span per LLM call, search call and page fetch.
Sentry for errors. Structured logs: audit_id, question_id, model, url, attempt,
latency_ms, cost_usd.