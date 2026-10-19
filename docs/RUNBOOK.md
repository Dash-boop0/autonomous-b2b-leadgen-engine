# Runbook

## Local (no Docker)

```bash
make dev                 # or ./scripts/dev.sh  (Windows: powershell -ExecutionPolicy Bypass -File scripts\dev.ps1)
RESET_DB=1 make dev      # start from a clean SQLite DB
SEED_PROFILE=full make reset-db   # pre-run the whole pipeline (approvals by the seeded reviewer, labelled as such)
```

Ports default to API 8000 / web 3000 and fall back to 8100 / 3100 if busy; override with `API_PORT` / `WEB_PORT`.
The SQLite DB lives in `backend/data/app.db`.

## Docker Compose

```bash
docker compose up --build            # project name "leadgen": db + api + web
API_PORT=8100 WEB_PORT=3100 docker compose up --build
docker compose down -v --rmi local   # stop, delete the volume and the built images
```

## Health and diagnostics

* `GET /health` -> `{"status":"ok","db":{"status":"ok","dialect":...,"seeded":true}}`. `degraded` means the DB is
  unreachable; the first request also runs migrations and seeds if the DB is empty.
* Workflows page: run steps, attempts, errors and outputs. Error queue: retry or dismiss failed steps.
* Audit log: filter by action (`agent.`, `approval.`, `send.`, `workflow.`), actor type or severity.

## Common operations

| Situation | Action |
|---|---|
| Something is sending unexpectedly (it cannot leave the system, but still) | Admin presses **STOP** in the top bar. Workflows, queueing and dispatch halt. Lift it from the same button. |
| A run is stuck in `running` | Click **Advance** on the run (the engine is request-driven). Backoff shows the next retry time. |
| A step failed | Fix the cause (e.g. re-enable an adapter), then **Retry** it from the error queue. |
| Crashed worker left a step `running` | Leases expire after `RUN_LEASE_SECONDS` (30 s); the next advance retries it. The Governance sweep also resets stale steps. |
| Someone asks to be forgotten | Contacts page -> **Erase** (admin): suppress + delete, audited. |
| Opt-out arrived by another channel | Governance -> Suppression list -> add email or domain; queued messages are cancelled immediately. |
| Start the demo over | Governance -> **Reset demo** (admin; audited). |

## Migrations

```bash
cd backend
.venv/bin/alembic upgrade head                                   # uses DATABASE_URL
.venv/bin/alembic revision --autogenerate -m "describe change"   # after editing app/models.py
```

## Tests (exactly what CI runs)

```bash
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/pytest
cd frontend && npm run typecheck && npm run lint -- --max-warnings=0 && npm test && npm run build
make e2e-postgres   # API e2e + acceptance through the Vercel entry point on the Compose Postgres
```
