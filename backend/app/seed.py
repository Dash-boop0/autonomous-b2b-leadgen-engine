"""Synthetic seed data. Everything here is fictional (example.* domains, +44 7700 900xxx numbers) and labelled.

Profiles:
  basic - users, adapters, approved-knowledge passages, suppression list, the offer, sample CSV uploads, and a
          Market-to-ICP run that stops at the human approval gate (the demo starts there).
  full  - basic + the whole simulated pipeline (approvals made by the seeded *reviewer* user, recorded as such)
          so every screen has data.
"""

from __future__ import annotations

import argparse
import hashlib

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import approvals, audit
from .config import FIXTURES_DIR, SAMPLES_DIR
from .models import (
    Approval,
    Campaign,
    CsvUpload,
    ICPVersion,
    KnowledgePassage,
    Offer,
    SourceAdapter,
    Suppression,
    User,
    utcnow,
)
from .security import SYSTEM, Actor, hash_password
from .system import get_state
from .workflows import engine as wf

DEMO_PASSWORD = "demo"  # documented demo credential; never use outside the demo
USERS = [
    ("admin", "Alex Admin (demo)", "admin"),
    ("operator", "Olu Operator (demo)", "operator"),
    ("reviewer", "Rae Reviewer (demo)", "reviewer"),
    ("viewer", "Vic Viewer (demo)", "viewer"),
]

ADAPTERS = [
    dict(
        id="csv_upload",
        name="CSV upload",
        source_type="csv",
        kind="accounts+contacts",
        mock=False,
        auth="none (user upload)",
        rate_limit_per_minute=600,
        cache_ttl_seconds=0,
        retention_days=365,
        terms_note="User attests they may process the uploaded data; stored in the DB, never written to disk.",
        fields=[
            "company_name",
            "website",
            "industry",
            "employees",
            "country",
            "business_model",
            "revenue_band",
            "account_domain",
            "full_name",
            "title",
            "email",
            "phone",
            "linkedin_url",
        ],
        failure_handling="Missing required columns rejects the file; bad rows are skipped with a reason.",
    ),
    dict(
        id="mock_registry",
        name="Companies registry API (mock)",
        source_type="registry",
        kind="accounts",
        mock=True,
        auth="none (mock); a real registry would use an API key from env",
        rate_limit_per_minute=30,
        cache_ttl_seconds=86400,
        retention_days=365,
        terms_note="Synthetic fixture standing in for an official registry API with re-use terms.",
        fields=[
            "registry_id",
            "name",
            "domain",
            "industry",
            "employee_count",
            "country",
            "business_model",
            "revenue_band",
            "last_filed",
        ],
        failure_handling="Rate-limit -> transient error, retried with backoff; disabled adapter -> permanent error.",
    ),
    dict(
        id="licensed_contacts",
        name="Licensed B2B contact dataset (mock)",
        source_type="licensed",
        kind="contacts",
        mock=True,
        auth="none (mock); a real dataset would use a licence key from env",
        rate_limit_per_minute=60,
        cache_ttl_seconds=86400,
        retention_days=180,
        terms_note="Synthetic fixture standing in for a dataset licensed for B2B prospecting; consent basis per row.",
        fields=["record_id", "account_domain", "full_name", "title", "email", "phone", "consent_basis"],
        failure_handling="Unknown domain -> empty result; rate-limit -> retried.",
    ),
    dict(
        id="public_web_fixture",
        name="Public web pages (local fixtures)",
        source_type="public_page",
        kind="web",
        mock=True,
        auth="none",
        rate_limit_per_minute=20,
        cache_ttl_seconds=86400,
        retention_days=90,
        allowlist=["example.com", "example.org", "example.net"],
        blocklist=["valemont.example.com"],
        terms_note="Reads local fixture pages only. robots rules and allow/blocklists are honoured; no login walls, "
        "CAPTCHAs or paywalls are ever bypassed.",
        fields=["url", "content"],
        failure_handling="robots-disallowed, blocked or missing pages are recorded, not retried.",
    ),
    dict(
        id="mock_dns",
        name="DNS MX lookup (mock)",
        source_type="api",
        kind="verification",
        mock=True,
        auth="none",
        rate_limit_per_minute=120,
        cache_ttl_seconds=3600,
        retention_days=365,
        terms_note="Fixture DNS. Optional real lookup via dnspython (DNS_PROVIDER=dnspython, untested).",
        fields=["domain", "has_mx"],
        failure_handling="DNS error -> check result 'error', status stays syntax_valid.",
    ),
    dict(
        id="mock_verifier",
        name="Mailbox verification provider (mock)",
        source_type="api",
        kind="verification",
        mock=True,
        auth="none (mock)",
        rate_limit_per_minute=60,
        cache_ttl_seconds=86400,
        retention_days=365,
        terms_note="Deterministic mock. 'provider_verified' from this adapter is labelled MOCK everywhere.",
        fields=["email", "result"],
        failure_handling="unknown/catch_all results never upgrade the status.",
    ),
]


def _knowledge(session: Session) -> None:
    text = (FIXTURES_DIR / "knowledge" / "northwind-approved.md").read_text()
    section, buf = None, []
    rows = []
    for line in text.splitlines():
        if line.startswith("## "):
            if section:
                rows.append((section, " ".join(buf).strip()))
            section, buf = line[3:].strip(), []
        elif section and line.strip():
            buf.append(line.strip())
    if section:
        rows.append((section, " ".join(buf).strip()))
    for sec, body in rows:
        session.add(
            KnowledgePassage(
                id=f"northwind-approved#{sec}",
                doc="Northwind approved statements (synthetic)",
                section=sec,
                text=body,
                approved=True,
                version="1.0",
                topics=sec.replace("-", " ").split(),
            )
        )


def seed_basic(session: Session) -> dict:
    st = get_state(session)
    users = {}
    for username, name, role in USERS:
        u = User(username=username, display_name=name, role=role, password_hash=hash_password(DEMO_PASSWORD))
        session.add(u)
        users[username] = u
    for a in ADAPTERS:
        session.add(SourceAdapter(**{"allowlist": [], "blocklist": [], **a}))
    _knowledge(session)
    session.add(
        Suppression(
            email_or_domain="olivia.grant@tidewell.example.org",
            reason="opt_out",
            created_by="seed",
            note="Synthetic: this contact opted out previously",
        )
    )
    session.add(
        Suppression(
            email_or_domain="mossgate.example.com",
            reason="manual",
            created_by="seed",
            note="Synthetic: company asked not to be contacted",
        )
    )
    session.flush()
    offer = Offer(
        name="Northwind Forecast",
        seller="Northwind Analytics (fictional)",
        description="Demand-forecasting and inventory-planning analytics for mid-market logistics, "
        "wholesale and manufacturing companies (fictional product).",
        target_countries=["GB", "IE", "NL"],
        keywords=[
            "logistics",
            "wholesale",
            "distribution",
            "manufacturing",
            "stockouts",
            "forecast",
            "warehouse",
            "erp",
            "planning",
            "demand",
        ],
        created_by=users["operator"].id,
    )
    session.add(offer)
    for fname, kind in (("accounts.csv", "accounts"), ("contacts.csv", "contacts")):
        content = (SAMPLES_DIR / fname).read_text()
        session.add(
            CsvUpload(
                filename=f"sample-{fname}",
                kind=kind,
                content=content,
                sha256=hashlib.sha256(content.encode()).hexdigest(),
                row_count=max(0, len(content.strip().splitlines()) - 1),
                uploaded_by="seed",
            )
        )
    st.seeded_profile, st.seeded_at = "basic", utcnow()
    audit.record(session, SYSTEM, "seed.loaded", "system", "1", after={"profile": "basic", "synthetic": True})
    session.commit()
    op = Actor("human", users["operator"].id, "operator")
    run = wf.start_run(session, "market_to_icp", {"offer_id": offer.id}, op, idempotency_key="seed:market_to_icp")
    wf.advance(session, run.id, budget_seconds=60)
    return {"offer_id": offer.id, "market_run_id": run.id}


def _user(session: Session, username: str) -> Actor:
    u = session.scalars(select(User).where(User.username == username)).one()
    return Actor("human", u.id, u.role)


def _approve_pending(session: Session, reviewer: Actor, action: str) -> Approval:
    apr = session.scalars(
        select(Approval)
        .where(Approval.status == "pending", Approval.action == action)
        .order_by(Approval.created_at.desc())
    ).first()
    assert apr is not None, f"no pending {action}"
    approvals.decide(session, apr.id, reviewer, "approved", "Approved by seeded demo reviewer (full seed profile)")
    session.commit()
    wf.resume_after_decision(session, apr)
    return apr


def simulate_full(session: Session) -> dict:
    """Drive the whole pipeline with the seeded operator/reviewer users (used by the 'full' seed profile)."""
    op, rv = _user(session, "operator"), _user(session, "reviewer")
    _approve_pending(session, rv, "icp.activate")
    icp = session.scalars(select(ICPVersion).where(ICPVersion.status == "approved")).one()
    acc_csv = session.scalars(select(CsvUpload).where(CsvUpload.kind == "accounts")).first()
    con_csv = session.scalars(select(CsvUpload).where(CsvUpload.kind == "contacts")).first()
    assert acc_csv and con_csv
    r = wf.start_run(
        session,
        "icp_to_accounts",
        {"icp_version_id": icp.id, "adapters": ["csv_upload", "mock_registry"], "csv_upload_id": acc_csv.id},
        op,
    )
    wf.advance(session, r.id, 60)
    r = wf.start_run(
        session,
        "account_to_contacts",
        {"icp_version_id": icp.id, "csv_upload_id": con_csv.id, "adapters": ["csv_upload", "licensed_contacts"]},
        op,
    )
    wf.advance(session, r.id, 60)
    camp = Campaign(
        name="Q4 demand-planning intro (synthetic)",
        icp_version_id=icp.id,
        segment_filter={"tiers": ["A", "B"]},
        created_by=op.actor_id,
    )
    session.add(camp)
    session.commit()
    r = wf.start_run(session, "lead_to_campaign", {"campaign_id": camp.id}, op)
    wf.advance(session, r.id, 60)
    _approve_pending(session, rv, "campaign.activate")
    r = wf.start_run(session, "campaign_to_conversation", {"campaign_id": camp.id}, op)
    wf.advance(session, r.id, 60)
    st = get_state(session)
    st.sim_clock_offset_days += 3
    session.commit()
    r = wf.start_run(session, "campaign_to_conversation", {"campaign_id": camp.id}, op)
    wf.advance(session, r.id, 60)
    r = wf.start_run(session, "performance_to_improvement", {"campaign_id": camp.id, "icp_version_id": icp.id}, op)
    wf.advance(session, r.id, 60)
    st.seeded_profile = "full"
    session.commit()
    return {"campaign_id": camp.id, "icp_version_id": icp.id}


def is_seeded(session: Session) -> bool:
    return (session.scalar(select(func.count()).select_from(User)) or 0) > 0


def seed(session: Session, profile: str = "basic") -> dict:
    out = seed_basic(session)
    if profile == "full":
        out.update(simulate_full(session))
    return out


def wipe(session: Session) -> None:
    from .db import Base

    for table in reversed(Base.metadata.sorted_tables):
        session.execute(table.delete())
    session.commit()


def main() -> None:  # pragma: no cover - CLI
    from .bootstrap import migrate
    from .config import get_settings
    from .db import session_factory

    p = argparse.ArgumentParser(description="Seed synthetic demo data")
    p.add_argument("--profile", choices=["basic", "full"], default=get_settings().seed_profile)
    p.add_argument("--reset", action="store_true", help="wipe all data first")
    a = p.parse_args()
    migrate()
    s = session_factory()()
    try:
        if a.reset:
            wipe(s)
        if is_seeded(s):
            print("Already seeded; use --reset to re-seed.")
            return
        print(seed(s, a.profile))
    finally:
        s.close()


if __name__ == "__main__":  # pragma: no cover
    main()
