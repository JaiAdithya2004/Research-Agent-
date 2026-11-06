# Deploying on Fly.io (durable setup)

The production setup in the main README uses Render, Vercel and Neon on free tiers. This is the alternative
when audits must survive restarts: a separate worker app, Redis, and Fly release commands.

## Overview

Three Fly apps, three images, one pipeline (`.github/workflows/ci.yml`). The deploy job runs only when
you start it by hand (Actions > ci > Run workflow) and only if every check passed:

| App | Config | Image | Role |
|---|---|---|---|
| `audit-api` | `fly.api.toml` | `docker/api.Dockerfile` | FastAPI. Runs migrations as its **release command**. |
| `audit-worker` | `fly.worker.toml` | `docker/worker.Dockerfile` | ARQ worker. The only process that fetches arbitrary URLs. |
| `audit-web` | `web/fly.toml` | `web/Dockerfile` | Next.js. Holds the API key server-side; the browser never sees it. |

Postgres and Redis are not defined here; point `DATABASE_URL` / `REDIS_URL` at managed ones
(`fly secrets set -a audit-api DATABASE_URL=... REDIS_URL=... API_KEY_PEPPER=...`). Secrets are never in the
repo or the images. The worker needs the same secrets except `API_KEY_PEPPER`.

First deploy, in this order (the API first, because it migrates the database):

```
fly deploy --config fly.api.toml          # runs `alembic upgrade head` on a temporary machine first
fly deploy --config fly.worker.toml
bash scripts/fly_worker_network_policy.sh audit-worker   # once, then restart the worker machines
fly ssh console -a audit-api -C "python -m app.auth create --name web"   # prints a key once
fly secrets set -a audit-web AUDIT_API_KEY=<that key>
(cd web && fly deploy)
```

**Migrations are a release step, never app boot.** `release_command` in `fly.api.toml` runs once per
deploy on a temporary machine with the new image; if it fails, the deploy stops and the old version
keeps serving. No process runs migrations on start, so ten API machines starting at once cannot race.

**Worker egress.** The worker fetches whatever URLs models cite, so it is deployed as its own app with a
Fly network policy that allows egress only on 80, 443, 5432, 6379 and 53. Be clear about what that is:
Fly policies filter by protocol and port, not by destination address. They do **not** stop the worker
reaching a private or metadata address on port 443. That protection is `app/fetch/safe_fetch.py`
(DNS check, IP-range blocking, pinned connection, per-redirect re-check), and CI runs its SSRF and
injection suites on every change. The policy is defence in depth for a compromised worker.

## Rollback

*Code.* Every deploy is a Fly release. To go back: `fly releases -a audit-api`, then
`fly deploy --config fly.api.toml --image <registry.fly.io/audit-api:deployment-...>`. **A code rollback
also re-runs the release command with the old image, which is a no-op if the schema is already at or past
the old image's revision (alembic only upgrades).** Roll back the API and the worker together.

*Database.* The rule that makes rollback safe: **migrations are additive first (expand), destructive
later (contract), and the previous release must work against the new schema.** Then rolling back code never
needs a schema downgrade, and the usual rollback is just the code step above. Only run
`alembic downgrade` when the migration itself is the problem, and then:

1. Take a backup or a Postgres snapshot first. `downgrade` can drop data.
2. Stop the worker (`fly scale count 0 -a audit-worker`) so nothing writes mid-change.
3. Deploy the old code **first**, then downgrade one step: `fly ssh console -a audit-api -C "python -m alembic downgrade -1"`.
4. Check `python -m alembic current`, then scale the worker back up.

What each migration's downgrade does:

| Revision | Upgrade | Downgrade | Safe to downgrade? |
|---|---|---|---|
| 0001-0004 | base tables, orchestrator columns, `eval_runs`, `audit_events` | drops them | **Loses that data.** Do not, outside a fresh environment. |
| 0005 | renames review action `accept` to `approve` (pauses the append-only trigger) | renames back | Yes, data-preserving. Old code needs the old name, so deploy old code first. |
| 0006 | `api_keys`, per-key idempotency | drops `api_keys` (**every issued key is gone**) and restores a global unique index on `idempotency_key` | Only if no two audits share an idempotency key. Check first: `SELECT idempotency_key, count(*) FROM audits GROUP BY 1 HAVING count(*) > 1;` Any row means the downgrade will fail. Prefer rolling forward. |

None of this has been exercised on a real Fly account. The upgrade/downgrade round trip for 0006 was run
against the local Postgres; the deploy configs, the images and the pipeline were written from Fly's
documentation and have not been deployed.
