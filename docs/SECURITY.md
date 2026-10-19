# Security

## Threat model (demo scope)

This is a portfolio demo with synthetic data, mock providers and dry-run sending. It is designed so the
*consequential* paths (approvals, sending, suppression, permissions) are enforced by deterministic code even if an
LLM or agent misbehaves.

| Control | Where | Tested by |
|---|---|---|
| Agents cannot approve, stop/reset, configure sources or erase data | `security.AGENT_FORBIDDEN`, `approvals.check_can_decide` | `test_approval_engine_rules`, `test_agents_cannot_hold_privileged_permissions` |
| No self-approval; four-eyes on ICP/campaign approvals | `approvals.py` | `test_approvals_enforced`, `test_approval_engine_rules` |
| Agents can only call their declared tools | `BaseAgent.tool` (denials audited) | `test_agent_tool_scope_confidence_and_completion` |
| Nothing is queued or dispatched without a human campaign approval | `outreach.queue_campaign`, `outreach.dispatch_due` | `test_full_pipeline_end_to_end`, `test_approvals_enforced` |
| Suppression checked at queue time and again at send time | `outreach.check_suppression` | `test_opt_outs_block_sending` |
| Emergency stop halts workflows, queueing and dispatch | `system.assert_not_stopped`, engine `tick` | `test_emergency_stop_blocks_workflows_and_sends` |
| Duplicate events / sends / follow-ups are impossible | unique keys on events, send_queue, drafts, CRM outbox | `test_duplicate_events_do_not_duplicate_sends_or_followups` |
| Role is read from the DB on every request; tokens are HMAC-signed and expire | `security.current_actor` | `test_tokens_are_signed_and_roles_come_from_db` |
| Audit trail is append-only | `audit.record` (no update/delete paths; the admin demo reset is the only wipe and is itself audited) | `test_reset_demo_is_admin_only_and_audited` |

## Demo authentication (not production auth)

Four seeded users (`admin`, `operator`, `reviewer`, `viewer`) share the documented password `demo`; passwords are
stored as PBKDF2 hashes. Session tokens are stateless HMAC-SHA256 tokens signed with `SECRET_KEY` (12 h TTL),
kept in `localStorage` by the UI. The `/api/auth/demo-users` endpoint lists users for the role switcher. **Before any
non-demo use**: replace with a real identity provider (OIDC), remove the demo-users endpoint, use httpOnly cookies
with CSRF protection, and add rate limiting on login.

## Secrets and configuration

No secrets are committed. `.env.example` lists every variable; `SECRET_KEY` has an obviously insecure dev default
that must be overridden in any shared deployment (Vercel/Compose docs set it). Optional provider keys
(`ANTHROPIC_API_KEY`) are read from the environment only and are never logged.

## Data protection

* All data is synthetic. CSV uploads are stored in the database, limited to 2 MB and parsed as text only.
* Erasure: admin action suppresses then deletes a contact. Retention sweep deletes contacts past the adapter's
  retention window. See [DATA-SOURCES.md](DATA-SOURCES.md).
* No real outreach: the only sender is the dry-run outbox. There is no SMTP/ESP code in the repository.

## Known gaps

No CSRF protection (bearer tokens, no cookies), no login rate limiting, no per-tenant isolation, CORS configured by
env only, and the mock LLM is not a defence against prompt injection in real model output (real providers are
optional and untested; their output is still only used for text, never for decisions).
