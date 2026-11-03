# Web frontend

Next.js (App Router), TypeScript strict, Tailwind v4, shadcn/ui style components, TanStack Query,
Vitest. Read `CONTRACT.md` (decisions and meanings the API types do not carry) and `../AGENTS.md`
(rules) first.

## Run it

```
cd web
copy .env.example .env.local     # then set AUDIT_API_KEY (and API_BASE_URL if not the default)
npm install
npm run dev                      # http://localhost:3000
```

To get a key and a backend, follow "Developing against the real backend" in `CONTRACT.md`
(`docker compose up -d --wait`, migrate, `uv run python -m app.auth create --name dev`, then
`uv run python -m app.api` and `uv run python -m app.worker`).

## The browser never holds the API key

The browser calls `/api/...` on the Next.js server. The route handler `src/app/api/[...path]/route.ts`
(logic in `src/server/proxy.ts`) forwards to `API_BASE_URL` and adds `X-API-Key: $AUDIT_API_KEY`.
Both variables are server-only: never name them `NEXT_PUBLIC_*`. The proxy forwards only an
allow-list of request headers (`Accept`, `Content-Type`, `Idempotency-Key`, `Last-Event-ID`), drops
cookies, does not follow redirects, and streams Server-Sent Events without buffering.

## The typed API client is generated

`openapi.json` is exported from the backend (`uv run python scripts/export_openapi.py` from the repo
root). `src/lib/api/schema.gen.ts` is generated from it and committed. Never edit it by hand.

```
npm run gen:api          # regenerate src/lib/api/schema.gen.ts from openapi.json
npm run gen:api:check    # regenerate to a temp file; exits 1 with a message if the committed file differs
```

When the backend API changes: export the schema, run `npm run gen:api`, commit both.

## Scripts

| script | what it does |
|---|---|
| `npm run dev` / `build` / `start` | Next.js |
| `npm run lint` | ESLint (next/core-web-vitals + typescript) |
| `npm run typecheck` | `tsc --noEmit`, strict |
| `npm test` | Vitest (jsdom). `fetch` and `EventSource` are mocked: no backend needed |
| `npm run gen:api` / `gen:api:check` | generate / verify the typed client |
| `npm run e2e` | Playwright against the built app (`npm run build` + `next start` on port 3100) and `e2e/fake-api.mjs` on port 4010. No backend needed |
| `npm run e2e:install` | download the Chromium that Playwright drives (once) |

## CI (`.github/workflows/web.yml`)

1. `uv run python scripts/export_openapi.py --check` (the committed `openapi.json` is current)
2. `npm ci`
3. `npm run gen:api:check` (the committed client is current)
4. `npm run lint`, `npm run typecheck`, `npm test`
5. `npm run build`

## Layout

- `src/app` routes only. `src/components` render. `src/hooks` fetch and hold state.
  `src/lib` plain, tested modules (no React). `src/server` server-only code (the proxy).
- Business logic never lives in components.
- `API_GAPS.md` lists things the UI needed that the API does not provide.

## End-to-end tests

`npm run e2e` starts `e2e/fake-api.mjs` (a plain node server that serves the shapes in
`openapi.json` and a scripted SSE stream) and the real built Next app pointed at it with
`API_BASE_URL` and a fake `AUDIT_API_KEY`. Specs: create audit, watch an audit complete (and
reconnect), review with undo, sources and the highlighted quote, axe on every page in both themes,
375px layouts (screenshots in `e2e/screenshots/`, gitignored). Vitest ignores `e2e/`.
`reuseExistingServer` is on outside CI, so a stale server on 3100/4010 is reused. What axe does and
does not cover is in `A11Y.md`.
