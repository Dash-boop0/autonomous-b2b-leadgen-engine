"""Source adapters for account/contact discovery. Each adapter declares fields, restrictions, auth, limits and
failure handling (mirrored in docs/DATA-SOURCES.md) and returns provenance-stamped records.

  csv_upload           - user-supplied CSV (stored in the DB)
  mock_registry        - synthetic companies-registry API (fixture JSON; stands in for e.g. an official registry API)
  licensed_contacts    - synthetic licensed B2B contact dataset (fixture JSON)
  public_web_fixture   - local fixture pages standing in for permitted public web pages (robots + allowlist honoured)
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from datetime import timedelta
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import FIXTURES_DIR
from ..models import AdapterUsage, FetchCache, SourceAdapter, utcnow
from ..normalise import normalise_domain


class AdapterError(RuntimeError):
    """Permanent adapter failure (bad config, disabled, policy)."""


class TransientAdapterError(RuntimeError):
    """Retryable adapter failure (rate limited, timeout)."""


@dataclass
class RawRecord:
    adapter: str
    source_type: str
    url_or_ref: str
    confidence: float
    payload: dict
    terms_note: str = ""
    retrieved_at: object = field(default_factory=utcnow)


def get_adapter(session: Session, adapter_id: str) -> SourceAdapter:
    a = session.get(SourceAdapter, adapter_id)
    if a is None:
        raise AdapterError(f"Unknown adapter {adapter_id}")
    if not a.enabled:
        raise AdapterError(f"Adapter {adapter_id} is disabled")
    return a


def consume_rate_limit(session: Session, adapter: SourceAdapter, n: int = 1) -> None:
    """Fixed one-minute window counter stored in the DB (works across serverless instances)."""
    now = utcnow()
    window = now.replace(second=0, microsecond=0)
    row = session.get(AdapterUsage, (adapter.id, window))
    if row is None:
        row = AdapterUsage(adapter=adapter.id, window_start=window, count=0)
        session.add(row)
    if row.count + n > adapter.rate_limit_per_minute:
        raise TransientAdapterError(f"Rate limit for {adapter.id} reached ({adapter.rate_limit_per_minute}/min)")
    row.count += n
    session.flush()


def domain_allowed(adapter: SourceAdapter, domain: str | None) -> tuple[bool, str]:
    d = normalise_domain(domain) or ""
    if any(d == b or d.endswith("." + b) for b in adapter.blocklist):
        return False, "blocklisted"
    if adapter.allowlist and not any(d == a or d.endswith("." + a) for a in adapter.allowlist):
        return False, "not on allowlist"
    return True, "ok"


# ---------------------------------------------------------------- CSV


CSV_ACCOUNT_FIELDS = ["company_name", "website", "industry", "employees", "country", "business_model", "revenue_band"]
CSV_CONTACT_FIELDS = ["account_domain", "full_name", "title", "email", "phone", "linkedin_url"]


def parse_csv(content: str, kind: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(content.lstrip("﻿")))
    required = {"company_name"} if kind == "accounts" else {"account_domain", "full_name"}
    headers = {h.strip().lower() for h in (reader.fieldnames or [])}
    missing = required - headers
    if missing:
        raise AdapterError(f"CSV is missing required column(s): {', '.join(sorted(missing))}")
    rows = []
    for i, row in enumerate(reader, start=2):
        clean = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        if not any(clean.values()):
            continue
        clean["_row"] = i
        rows.append(clean)
    return rows


def csv_records(adapter: SourceAdapter, upload_id: str, filename: str, rows: list[dict]) -> list[RawRecord]:
    return [RawRecord(adapter.id, "csv", f"csv:{filename}#row{r['_row']}", 0.7, r, adapter.terms_note) for r in rows]


# ---------------------------------------------------------------- fixture-backed APIs


@lru_cache
def _json_fixture(name: str):
    return json.loads((FIXTURES_DIR / name).read_text())


def registry_search(
    session: Session, adapter: SourceAdapter, industries: list[str], countries: list[str]
) -> list[RawRecord]:
    consume_rate_limit(session, adapter)
    out = []
    for c in _json_fixture("registry.json")["companies"]:
        if industries and c["industry"] not in industries:
            continue
        if countries and c["country"] not in countries:
            continue
        ok, _ = domain_allowed(adapter, c.get("domain"))
        if not ok:
            continue
        out.append(RawRecord(adapter.id, "registry", f"registry:{c['registry_id']}", 0.9, c, adapter.terms_note))
    return out


def licensed_contacts_for(session: Session, adapter: SourceAdapter, domain: str) -> list[RawRecord]:
    consume_rate_limit(session, adapter)
    rows = [c for c in _json_fixture("contacts_dataset.json")["contacts"] if c["account_domain"] == domain]
    return [RawRecord(adapter.id, "licensed", f"licensed:{c['record_id']}", 0.75, c, adapter.terms_note) for c in rows]


# ---------------------------------------------------------------- public web (fixture pages)


def fetch_public_page(session: Session, adapter: SourceAdapter, domain: str, path: str = "/news") -> dict:
    """Return {status, content, cached}. Honours allowlist/blocklist, robots rules, rate limit and a DB cache.
    Reads local fixture files only: no network access happens in this adapter."""
    url = f"https://{domain}{path}"
    ok, why = domain_allowed(adapter, domain)
    if not ok:
        return {"url": url, "status": "blocked", "reason": why, "content": "", "cached": False}
    cached = session.get(FetchCache, url)
    if cached and cached.fetched_at >= utcnow() - timedelta(seconds=adapter.cache_ttl_seconds):
        return {"url": url, "status": cached.status, "content": cached.content, "cached": True, "reason": "cache"}
    robots = _json_fixture("web/robots.json")
    disallow = robots.get(domain, [])
    if any(path.startswith(p) for p in disallow):
        status, content, reason = "disallowed", "", "robots.txt disallows this path"
    else:
        consume_rate_limit(session, adapter)
        f = FIXTURES_DIR / "web" / f"{domain}.html"
        if f.exists():
            status, content, reason = "ok", f.read_text(), "fixture page"
        else:
            status, content, reason = "not_found", "", "no page"
    if cached:
        cached.status, cached.content, cached.fetched_at = status, content, utcnow()
    else:
        session.add(FetchCache(url=url, status=status, content=content, fetched_at=utcnow()))
    session.flush()
    return {"url": url, "status": status, "content": content, "cached": False, "reason": reason}


def list_cached(session: Session) -> list[FetchCache]:
    return list(session.scalars(select(FetchCache).order_by(FetchCache.fetched_at.desc())))
