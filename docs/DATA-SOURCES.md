# Data sources

Every account and contact carries `source_ids` pointing at `source_records` (adapter, source_type, url_or_ref,
retrieved_at, terms_note, confidence, raw_hash). Adapters are configured in the `source_adapters` table and in the
UI (Source adapters page): enabled flag, allowlist, blocklist, rate limit, cache TTL and retention days.
**All demo data is synthetic** (fictional companies on `example.com/.org/.net`, `+44 7700 900xxx` numbers).

| Adapter | Type | Mock? | Fields | Auth | Limits | Restrictions | Failure handling |
|---|---|---|---|---|---|---|---|
| `csv_upload` | csv | real (user file) | accounts: company_name*, website, industry, employees, country, business_model, revenue_band; contacts: account_domain*, full_name*, title, email, phone, linkedin_url | none (user upload; RBAC `sources.import`) | 2 MB per file | Uploader attests they may process the data; stored in the DB (no disk writes); blocklisted domains skipped | Missing required columns rejects the file (422); bad rows are skipped with a reason in the run output |
| `mock_registry` | registry | **mock** (fixture `registry.json`) | registry_id, name, domain, industry, employee_count, country, business_model, revenue_band, last_filed | none (a real registry API key would come from env) | 30 req/min (DB fixed window) | Stands in for an official registry API whose terms allow re-use | Rate limit -> transient error, retried with backoff; disabled adapter -> permanent error, error queue |
| `licensed_contacts` | licensed | **mock** (fixture `contacts_dataset.json`) | record_id, account_domain, full_name, title, email, phone, consent_basis | none (licence key from env in a real integration) | 60 req/min | Only for accounts already selected by the approved ICP; consent basis stored per contact | Unknown domain -> empty result; rate limit -> retried |
| `public_web_fixture` | public_page | **mock** (local fixture pages) | url, content | none | 20 req/min, 24 h DB cache | Allowlist `example.com/.org/.net`; blocklist (competitor domain); robots rules from `fixtures/web/robots.json` are honoured; never logs in, solves CAPTCHAs or passes paywalls | `blocked`, `disallowed`, `not_found` are recorded per account and never retried |
| `mock_dns` | api | **mock** | domain, has_mx | none | 120 req/min | Optional real lookup with `DNS_PROVIDER=dnspython` (needs the `dns` extra; **untested**) | DNS error -> check result `error`; status stays `syntax_valid` |
| `mock_verifier` | api | **mock** | email, result (valid/invalid/catch_all/unknown) | none | 60 req/min | `provider_verified` from this adapter is labelled MOCK in the UI and in `verification_checks` | `unknown`/`catch_all` never upgrade status; `invalid` keeps `domain_has_mx` and blocks qualification |

Internal (non-source) adapters: dry-run email sender (writes `send_queue`, returns `dryrun-…` ids; **no real sender
exists**), simulated mailbox events (`fixtures/reply_scenarios.json`, deterministic, idempotent ids), mock CRM
(`crm_outbox` table), knowledge base (`fixtures/knowledge/northwind-approved.md`, approved passages only), market and
competitor notes (`fixtures/market/*.md`, synthetic).

## How a real public-web fetcher would behave

The fixture adapter implements the same policy surface a real fetcher must: check allow/blocklists, fetch and cache
`robots.txt` and refuse disallowed paths, enforce a per-adapter rate limit across instances (DB counter), cache
responses (TTL), identify itself with a descriptive user agent, never send credentials, and record every outcome as
a source record or a failure. Anything behind a login, CAPTCHA, paywall or other technical control is out of scope.

## Provenance, freshness, retention, deletion

* **Provenance**: `source_records` are idempotent on `(adapter, raw_hash)`; merges during dedup add the new source id
  to the account and write `merge` evidence.
* **Freshness**: `last_verified_at` feeds the `data_freshness` scoring criterion (≤30 d full credit, ≤90 d 0.7,
  ≤180 d 0.4, older 0.1).
* **Retention**: each adapter has `retention_days`; the Governance sweep workflow deletes contacts whose every source
  is older than its retention period (engaged contacts are kept for human review).
* **Deletion/erasure**: `POST /api/contacts/{id}/erase` (admin) adds the address to the suppression list (reason
  `legal`, so it can never be re-imported into outreach) and deletes the contact; both are audited.
