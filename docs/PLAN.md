# Build plan — autonomous-b2b-leadgen-engine

This is the plan written before implementation, kept up to date as the build progressed. The live implementation status
table is in the README.

## Architecture (summary)

```
Next.js UI (frontend/)  ──typed API client──▶  FastAPI (backend/app)
                                                 │
                     ┌───────────────────────────┼─────────────────────────────┐
                     ▼                           ▼                             ▼
            Workflow engine (DB-backed)    Deterministic core              Agents (20)
            workflow_runs / workflow_steps scoring, verification levels,   typed I/O, scoped tools,
            idempotency, retries+backoff,  dedup, suppression, RBAC,       evidence, confidence →
            error queue, pause/resume,     approvals, frequency caps,      abstain/escalate
            emergency stop                 metrics formulas
                     │                           │                             │
                     └──────────────► SQLAlchemy 2 (SQLite default / PostgreSQL) ◄──── Adapters (mock default)
                                       + append-only audit_events                  CSV, registry, fixture web,
                                                                                  DNS, verifier, email outbox,
                                                                                  CRM, LLM (Mock/Ollama/Anthropic)
```

Rule of thumb: **LLM providers only synthesise, classify and draft**. Everything that decides — scoring, verification
level, suppression, approvals, permissions, state transitions, caps — is plain Python with unit tests.

## File tree (top level)

```
backend/   FastAPI app, agents, workflows, adapters, Alembic migrations, pytest suite
frontend/  Next.js App Router + TypeScript UI, Vitest unit tests
docs/      PLAN, ARCHITECTURE, DATA-SOURCES, SECURITY, RUNBOOK, DEMO, screenshots
scripts/   dev.sh / dev.ps1 one-command local run, demo helpers
```

## Dependencies

Backend: fastapi, uvicorn, pydantic v2, pydantic-settings, sqlalchemy 2, alembic, python-multipart, httpx (tests and
optional providers), psycopg (PostgreSQL in Docker). Dev: pytest, ruff. Optional: dnspython (real MX lookup).
Frontend: next, react, react-dom, typescript, eslint + eslint-config-next, vitest. No UI kit.

## Phases (vertical slices)

1. Core: DB models + migration, settings, demo auth/RBAC, audit, approval engine, emergency stop, workflow engine.
2. Market-to-ICP: market research, personas, competitor gaps, ICP builder, approval gate, ICP versioning + editor.
3. ICP-to-accounts: CSV + mock registry discovery, normalisation, dedup, fixture public-web research, buying signals,
   deterministic scoring with explanations.
4. Account-to-contacts: role matching, contact discovery, enrichment, honest verification levels, qualification,
   suppression checks, leads.
5. Lead-to-campaign: segmentation, plan, evidence-cited drafts, compliance checks, approval, dry-run send queue.
6. Campaign-to-conversation: simulated delivery/reply events (idempotent), classification, suppression, follow-up
   cancellation, knowledge-grounded responses with abstain/escalate, mock CRM update.
7. Performance-to-improvement: metrics with tested formulas/denominators, segment/source analysis, ICP recommendations,
   human review, new ICP version.
8. UI for all of the above, docs, CI, Docker, Playwright clean-setup demo.
9. (Added mid-build) Vercel serverless shape: request-driven ticks with leases and a time budget, first-request
   migrate + seed under a Postgres advisory lock, no filesystem writes, admin demo reset, DEPLOY-VERCEL.md.

## Acceptance tests (named in backend/tests/test_acceptance.py)

| Brief requirement | Test |
|---|---|
| Clean setup runs without paid credentials | `test_clean_setup_without_paid_credentials` |
| Approve an ICP, import/discover accounts, dedup and score them | `test_approve_icp_import_dedup_and_score` |
| Contacts matched to buyer roles with accurately labelled verification | `test_contacts_buyer_roles_and_honest_verification_levels` |
| Evidence-backed drafts and a simulated campaign work | `test_evidence_backed_drafts_and_simulated_campaign` |
| Opt-outs block sending | `test_opt_outs_block_sending` |
| Duplicate events don't create duplicate sends/follow-ups | `test_duplicate_events_do_not_duplicate_sends_or_followups` |
| Approvals enforced | `test_approvals_enforced` |
| Metrics have tested formulas and denominators | `test_metrics_formulas_and_denominators` |
| Sources, agent actions, errors and approvals traceable | `test_sources_agent_actions_errors_approvals_traceable` |
| Emergency stop halts workflows and sending | `test_emergency_stop_blocks_workflows_and_sends` |
| Frequency caps defer sends | `test_frequency_caps_defer_sends` |
| Full API-level end-to-end | `tests/test_e2e_api.py::test_full_pipeline_end_to_end` |

## Ports

API defaults to 8000 and web to 3000; both are configurable (`API_PORT`, `WEB_PORT`). `scripts/dev.sh` falls back to
8100/3100 if the defaults are busy. Docker Compose project name: `leadgen`.
