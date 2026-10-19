# Deploy a free hosted demo on Vercel + Neon

> **Status: not deployed or verified on Vercel.** The serverless shape was verified locally: the Vercel entry point
> (`backend/api/index.py`) was imported and the full API end-to-end and acceptance tests were run through it against
> PostgreSQL 16 with `VERCEL=1` (no connection pool), and four concurrent cold starts on an empty database produced
> exactly one seed. Vercel-specific behaviour (routing, bundling, timeouts) has not been exercised.

The repo deploys as **two Vercel projects that import the same GitHub repository**:

| Project | Root Directory | Framework preset | What it serves |
|---|---|---|---|
| `autonomous-b2b-leadgen-engine-api` | `backend` | Other | FastAPI as one Python serverless function (`api/index.py`) |
| `autonomous-b2b-leadgen-engine-web` | `frontend` | Next.js | The UI (default build) |

## 1. Accounts (free)

1. Create a Vercel account at <https://vercel.com/signup> and choose **Continue with GitHub**.
2. Make sure the repository `Dash-boop0/autonomous-b2b-leadgen-engine` is on GitHub (public or private).

## 2. API project + Neon Postgres

1. Vercel dashboard -> **Add New… -> Project** -> import the repository.
2. **Project Name**: `autonomous-b2b-leadgen-engine-api`. **Root Directory**: `backend`. **Framework Preset**: `Other`.
   Leave build/output settings empty. Vercel installs `backend/requirements.txt` (pinned runtime dependencies only)
   and uses `backend/vercel.json`, which rewrites every path to `/api/index` and includes `app/**`, `alembic/**` and
   `alembic.ini` in the function bundle.
3. Deploy once (it will report `degraded` until the database exists).
4. In the API project: **Storage -> Create Database -> Neon (Marketplace) -> Free plan**, connect it to the project
   for all environments. The integration injects `DATABASE_URL`. `postgres://` and `postgresql://` URLs are both
   accepted; the app switches them to the `postgresql+psycopg://` driver and appends `sslmode=require` if absent.
5. **Settings -> Environment Variables** (API project):

   | Name | Value |
   |---|---|
   | `DATABASE_URL` | injected by the Neon integration |
   | `APP_MODE` | `demo` |
   | `SECRET_KEY` | any long random string, e.g. output of `openssl rand -hex 32` |
   | `CORS_ORIGINS` | `https://autonomous-b2b-leadgen-engine-web.vercel.app` (comma-separate extra origins) |

   Optional: `CORS_ORIGIN_REGEX=https://autonomous-b2b-leadgen-engine-web-.*\.vercel\.app` for preview deployments,
   `SEED_PROFILE=full` to pre-run the whole pipeline on first boot.
6. **Deployments -> Redeploy**. Then open `https://autonomous-b2b-leadgen-engine-api.vercel.app/health`. The first
   request runs Alembic migrations and seeds the synthetic data if the database is empty (under a Postgres advisory
   lock, so concurrent cold starts are safe). Expect `{"status":"ok", "db":{"status":"ok","dialect":"postgresql","seeded":true}, ...}`.

## 3. Web project

1. **Add New… -> Project** -> import the same repository again.
2. **Project Name**: `autonomous-b2b-leadgen-engine-web`. **Root Directory**: `frontend`. **Framework Preset**:
   `Next.js` (default build command and output).
3. Environment variable: `NEXT_PUBLIC_API_URL=https://autonomous-b2b-leadgen-engine-api.vercel.app` (it is baked in at
   build time, so redeploy after changing it).
4. Deploy, open `https://autonomous-b2b-leadgen-engine-web.vercel.app`, sign in as `operator` / `demo`.

If Vercel assigns different domains, update `CORS_ORIGINS` (API) and `NEXT_PUBLIC_API_URL` (web) and redeploy both.

## Deploy from the CLI (alternative)

```bash
npm i -g vercel            # or: npx vercel
cd backend  && vercel link && vercel env add APP_MODE && vercel env add SECRET_KEY && vercel env add CORS_ORIGINS && vercel --prod
cd ../frontend && vercel link && vercel env add NEXT_PUBLIC_API_URL && vercel --prod
```

## How the app fits the serverless model

* **Stateless requests.** No background threads are required. Workflows advance through request-driven ticks:
  starting a run executes steps until the next approval gate, a backoff, or a 7 s budget (`TICK_BUDGET_SECONDS`);
  the UI keeps calling `POST /api/runs/{id}/advance` while a run is `running`. A per-run lease prevents two requests
  executing the same run; a step interrupted by a function timeout is retried after the lease expires (30 s).
* **No filesystem writes.** CSV uploads, the fetch cache, rate-limit counters and all state live in Postgres. If
  `DATABASE_URL` is missing on Vercel the app falls back to `sqlite:////tmp/leadgen.db` (ephemeral, per instance;
  only useful for smoke tests).
* **Connections.** On Vercel (`VERCEL` env var set) SQLAlchemy uses `NullPool`, opening a connection per request.
  Neon's pooled connection string also works.
* **Demo reset.** Admins can re-seed from Governance -> *Reset demo* (audited).

## Limits to know

* **Hobby plan is for personal, non-commercial use.** Use a Pro plan for anything commercial.
* **Function duration.** `backend/vercel.json` sets `maxDuration: 10` seconds and the engine budgets 7 s per request;
  the plan's maximum duration may differ, so check Vercel's current limits before raising it.
* **Cold starts.** The first request after idle loads Python and connects to the database; the very first request on
  a new database also migrates and seeds (several seconds). Neon's free tier suspends idle compute, which adds
  latency to the first query after a pause.
* **Bundle size.** Python functions have a bundle size limit; the pinned runtime deps (FastAPI, SQLAlchemy, Alembic,
  psycopg binary) are well within it, but adding heavy optional extras (e.g. `anthropic`) increases it.
* **Optional providers.** Mock providers are the default and need no credentials. Ollama cannot run on Vercel;
  the Anthropic provider is optional and untested.
