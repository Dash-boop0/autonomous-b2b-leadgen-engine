"""SQLAlchemy models. Field names of the shared entities follow docs/SHARED-INTERFACES (Account, Contact, SourceRecord,
Evidence, QualificationResult, Lead, Approval, AuditEvent, Suppression) exactly; extra columns are leadgen-specific."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _id(prefix: str):
    return mapped_column(String(40), primary_key=True, default=lambda: new_id(prefix))


class UTCDateTime(TypeDecorator):
    """Always returns timezone-aware UTC datetimes, on SQLite (which stores naive values) and on PostgreSQL."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        value = value.astimezone(UTC)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


TS = UTCDateTime()


# ---------------------------------------------------------------- identity, system


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = _id("usr")
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20))  # admin | operator | reviewer | viewer
    password_hash: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class SystemState(Base):
    __tablename__ = "system_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    emergency_stop: Mapped[bool] = mapped_column(Boolean, default=False)
    stop_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    stop_set_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    stop_set_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    send_mode: Mapped[str] = mapped_column(String(20), default="dry_run")
    seeded_profile: Mapped[str | None] = mapped_column(String(20), nullable=True)
    seeded_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    # Simulated clock offset (days) used by the dry-run sender to make follow-ups due without waiting.
    sim_clock_offset_days: Mapped[int] = mapped_column(Integer, default=0)


# ---------------------------------------------------------------- sources & provenance


class SourceAdapter(Base):
    __tablename__ = "source_adapters"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # adapter key, e.g. "csv_upload"
    name: Mapped[str] = mapped_column(String(120))
    source_type: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(20))  # accounts | contacts | web | verification
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    mock: Mapped[bool] = mapped_column(Boolean, default=True)
    auth: Mapped[str] = mapped_column(String(80), default="none")
    allowlist: Mapped[list] = mapped_column(JSON, default=list)
    blocklist: Mapped[list] = mapped_column(JSON, default=list)
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, default=60)
    cache_ttl_seconds: Mapped[int] = mapped_column(Integer, default=86400)
    retention_days: Mapped[int] = mapped_column(Integer, default=365)
    terms_note: Mapped[str] = mapped_column(Text, default="")
    fields: Mapped[list] = mapped_column(JSON, default=list)
    failure_handling: Mapped[str] = mapped_column(Text, default="")


class SourceRecord(Base):
    __tablename__ = "source_records"
    id: Mapped[str] = _id("src")
    adapter: Mapped[str] = mapped_column(String(40))
    source_type: Mapped[str] = mapped_column(String(20))  # csv|api|registry|directory|public_page|licensed|synthetic
    url_or_ref: Mapped[str] = mapped_column(Text)
    retrieved_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    terms_note: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    raw_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    __table_args__ = (UniqueConstraint("adapter", "raw_hash", name="uq_source_adapter_hash"),)


class CsvUpload(Base):
    """CSV files are stored in the database (no filesystem writes; serverless friendly)."""

    __tablename__ = "csv_uploads"
    id: Mapped[str] = _id("csv")
    filename: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))  # accounts | contacts
    content: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_by: Mapped[str] = mapped_column(String(40))
    uploaded_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    consumed_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class AdapterUsage(Base):
    """Fixed-window request counter per adapter (rate limiting)."""

    __tablename__ = "adapter_usage"
    adapter: Mapped[str] = mapped_column(String(40), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(TS, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)


class FetchCache(Base):
    __tablename__ = "fetch_cache"
    url: Mapped[str] = mapped_column(String(500), primary_key=True)
    status: Mapped[str] = mapped_column(String(20))  # ok | disallowed | not_found | blocked
    content: Mapped[str] = mapped_column(Text, default="")
    fetched_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Evidence(Base):
    __tablename__ = "evidence"
    id: Mapped[str] = _id("ev")
    entity_type: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[str] = mapped_column(String(40), index=True)
    claim: Mapped[str] = mapped_column(Text)
    excerpt: Mapped[str] = mapped_column(Text, default="")
    source_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    created_by: Mapped[str] = mapped_column(String(10), default="agent")  # agent | human
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    agent: Mapped[str | None] = mapped_column(String(60), nullable=True)
    kind: Mapped[str] = mapped_column(String(30), default="fact")  # fact | signal | merge | research


class KnowledgePassage(Base):
    __tablename__ = "knowledge_passages"
    id: Mapped[str] = mapped_column(String(60), primary_key=True)
    doc: Mapped[str] = mapped_column(String(120))
    section: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)
    approved: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[str] = mapped_column(String(20), default="1.0")
    topics: Mapped[list] = mapped_column(JSON, default=list)


# ---------------------------------------------------------------- strategy


class Offer(Base):
    __tablename__ = "offers"
    id: Mapped[str] = _id("off")
    name: Mapped[str] = mapped_column(String(200))
    seller: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    target_countries: Mapped[list] = mapped_column(JSON, default=list)
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class ResearchArtifact(Base):
    __tablename__ = "research_artifacts"
    id: Mapped[str] = _id("res")
    run_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    offer_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    kind: Mapped[str] = mapped_column(String(30))  # segment | persona | competitor_gap | icp_recommendation | analysis
    name: Mapped[str] = mapped_column(String(200))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    agent: Mapped[str] = mapped_column(String(60))
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class ICPVersion(Base):
    __tablename__ = "icp_versions"
    id: Mapped[str] = _id("icp")
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(20), default="draft"
    )  # draft|pending_approval|approved|superseded|rejected
    offer_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    definition: Mapped[dict] = mapped_column(JSON, default=dict)
    weights: Mapped[dict] = mapped_column(JSON, default=dict)
    hard_disqualifiers: Mapped[list] = mapped_column(JSON, default=list)
    tier_thresholds: Mapped[dict] = mapped_column(JSON, default=dict)
    rationale: Mapped[str] = mapped_column(Text, default="")
    change_summary: Mapped[str] = mapped_column(Text, default="")
    parent_version_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_by: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    approval_id: Mapped[str | None] = mapped_column(String(40), nullable=True)


# ---------------------------------------------------------------- accounts, contacts, leads


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[str] = _id("acc")
    name: Mapped[str] = mapped_column(String(200))
    domain: Mapped[str | None] = mapped_column(String(200), unique=True, nullable=True)
    industry: Mapped[str | None] = mapped_column(String(80), nullable=True)
    employee_band: Mapped[str | None] = mapped_column(String(20), nullable=True)
    revenue_band: Mapped[str | None] = mapped_column(String(20), nullable=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    region: Mapped[str | None] = mapped_column(String(60), nullable=True)
    business_model: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_ids: Mapped[list] = mapped_column(JSON, default=list)
    first_seen_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    last_verified_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    # leadgen-specific
    dedup_key: Mapped[str] = mapped_column(String(200), unique=True)
    signals: Mapped[list | None] = mapped_column(JSON, nullable=True, default=None)  # None = not researched yet
    qualification_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=True)


class Contact(Base):
    __tablename__ = "contacts"
    id: Mapped[str] = _id("con")
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    full_name: Mapped[str] = mapped_column(String(200))
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    seniority: Mapped[str | None] = mapped_column(String(30), nullable=True)
    department: Mapped[str | None] = mapped_column(String(60), nullable=True)
    buyer_role: Mapped[str] = mapped_column(String(20), default="unknown")
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    email_status: Mapped[str] = mapped_column(String(30), default="unknown")
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(300), nullable=True)
    source_ids: Mapped[list] = mapped_column(JSON, default=list)
    consent_basis: Mapped[str] = mapped_column(String(60), default="legitimate_interest_b2b")
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False)
    suppression_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # leadgen-specific
    dedup_key: Mapped[str] = mapped_column(String(250), unique=True)
    verification_checks: Mapped[list] = mapped_column(JSON, default=list)
    role_match_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class QualificationResult(Base):
    __tablename__ = "qualification_results"
    id: Mapped[str] = _id("qr")
    entity: Mapped[str] = mapped_column(String(10))  # account | contact | lead
    entity_id: Mapped[str] = mapped_column(String(40), index=True)
    model_version: Mapped[str] = mapped_column(String(60))
    icp_version_id: Mapped[str] = mapped_column(String(40))
    hard_disqualifiers: Mapped[list] = mapped_column(JSON, default=list)
    criteria: Mapped[list] = mapped_column(JSON, default=list)
    total_score_0_100: Mapped[float] = mapped_column(Float)
    tier: Mapped[str] = mapped_column(String(15))
    explained_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    explanation: Mapped[str] = mapped_column(Text, default="")
    missing_data: Mapped[list] = mapped_column(JSON, default=list)


class Lead(Base):
    __tablename__ = "leads"
    id: Mapped[str] = _id("lead")
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), unique=True)
    stage: Mapped[str] = mapped_column(String(30), default="new")
    # new|qualified|disqualified|in_campaign|replied|interested|not_interested|unsubscribed|bounced
    owner: Mapped[str | None] = mapped_column(String(40), nullable=True)
    qualification_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    suppression_checked_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    segment: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Suppression(Base):
    __tablename__ = "suppressions"
    id: Mapped[str] = _id("sup")
    email_or_domain: Mapped[str] = mapped_column(String(200), unique=True)
    reason: Mapped[str] = mapped_column(String(20))  # opt_out|bounce|complaint|manual|legal
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    created_by: Mapped[str] = mapped_column(String(60), default="system")
    note: Mapped[str] = mapped_column(Text, default="")


# ---------------------------------------------------------------- governance


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = _id("apr")
    subject_type: Mapped[str] = mapped_column(String(30))
    subject_id: Mapped[str] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(60))
    requested_by_type: Mapped[str] = mapped_column(String(10))  # agent | human
    requested_by_id: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending|approved|rejected|expired
    decided_by: Mapped[str | None] = mapped_column(String(60), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    policy_id: Mapped[str] = mapped_column(String(60))
    # leadgen-specific
    initiated_by: Mapped[str | None] = mapped_column(String(60), nullable=True)  # human who started the run
    run_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    step_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    __table_args__ = (UniqueConstraint("subject_type", "subject_id", "action", "run_id", name="uq_approval_subject"),)


class AuditEvent(Base):
    """Append-only. Nothing in the codebase updates or deletes rows (except the explicit admin demo reset)."""

    __tablename__ = "audit_events"
    id: Mapped[str] = _id("aud")
    at: Mapped[datetime] = mapped_column(TS, default=utcnow, index=True)
    actor_type: Mapped[str] = mapped_column(String(10))  # agent | human | system
    actor_id: Mapped[str] = mapped_column(String(60))
    action: Mapped[str] = mapped_column(String(80), index=True)
    subject_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    subject_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    before: Mapped[Any] = mapped_column(JSON, nullable=True)
    after: Mapped[Any] = mapped_column(JSON, nullable=True)
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    run_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    step_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    severity: Mapped[str] = mapped_column(String(10), default="info")  # info | warn | error


# ---------------------------------------------------------------- campaigns & conversations


class Campaign(Base):
    __tablename__ = "campaigns"
    id: Mapped[str] = _id("cmp")
    name: Mapped[str] = mapped_column(String(200))
    icp_version_id: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    # draft|pending_approval|approved|active|paused|completed|rejected|stopped
    segment_filter: Mapped[dict] = mapped_column(JSON, default=dict)
    sequence: Mapped[list] = mapped_column(JSON, default=list)
    frequency_cap: Mapped[dict] = mapped_column(JSON, default=dict)
    schedule: Mapped[dict] = mapped_column(JSON, default=dict)
    strategy: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    approval_id: Mapped[str | None] = mapped_column(String(40), nullable=True)


class Draft(Base):
    __tablename__ = "drafts"
    id: Mapped[str] = _id("dft")
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    lead_id: Mapped[str] = mapped_column(String(40))
    contact_id: Mapped[str] = mapped_column(String(40))
    step_no: Mapped[int] = mapped_column(Integer)
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    personalisation_level: Mapped[str] = mapped_column(String(20))  # evidence_grounded | generic
    policy_checks: Mapped[list] = mapped_column(JSON, default=list)
    policy_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(15), default="draft")  # draft|rejected|queued|cancelled|sent
    llm_provider: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    __table_args__ = (UniqueConstraint("campaign_id", "contact_id", "step_no", name="uq_draft_step"),)


class SendQueueItem(Base):
    __tablename__ = "send_queue"
    id: Mapped[str] = _id("snd")
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    draft_id: Mapped[str] = mapped_column(String(40))
    contact_id: Mapped[str] = mapped_column(String(40), index=True)
    lead_id: Mapped[str] = mapped_column(String(40))
    step_no: Mapped[int] = mapped_column(Integer)
    to_email: Mapped[str] = mapped_column(String(200))
    scheduled_for: Mapped[datetime] = mapped_column(TS)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|sent_simulated|cancelled|blocked
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    mode: Mapped[str] = mapped_column(String(10), default="dry_run")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class MessageEvent(Base):
    __tablename__ = "message_events"
    id: Mapped[str] = _id("evt")
    provider: Mapped[str] = mapped_column(String(40))
    provider_event_id: Mapped[str] = mapped_column(String(120))
    send_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    contact_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    event_type: Mapped[str] = mapped_column(String(20))  # delivered|bounced|replied|unsubscribed|complaint
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    received_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    simulated: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (UniqueConstraint("provider", "provider_event_id", name="uq_event_provider_id"),)


class Reply(Base):
    __tablename__ = "replies"
    id: Mapped[str] = _id("rpl")
    event_id: Mapped[str] = mapped_column(String(40), unique=True)
    contact_id: Mapped[str] = mapped_column(String(40))
    campaign_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    body: Mapped[str] = mapped_column(Text)
    classification: Mapped[str] = mapped_column(String(30))
    confidence: Mapped[float] = mapped_column(Float)
    classifier: Mapped[str] = mapped_column(String(30))
    needs_human: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="new")  # new|auto_handled|needs_review|escalated|resolved
    response_status: Mapped[str] = mapped_column(String(20), default="none")  # none|drafted|abstained|escalated
    suggested_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_citations: Mapped[list] = mapped_column(JSON, default=list)
    escalation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    triaged_by: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class CrmOutbox(Base):
    """Mock CRM adapter target: the updates a real CRM integration would push."""

    __tablename__ = "crm_outbox"
    id: Mapped[str] = _id("crm")
    entity_type: Mapped[str] = mapped_column(String(20))
    entity_id: Mapped[str] = mapped_column(String(40))
    operation: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


# ---------------------------------------------------------------- workflow engine


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"
    id: Mapped[str] = _id("run")
    workflow: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20), default="running")
    # running|waiting_approval|paused|completed|failed|cancelled
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True, nullable=True)
    created_by: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(40), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class WorkflowStep(Base):
    __tablename__ = "workflow_steps"
    id: Mapped[str] = _id("stp")
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True)
    idx: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(60))
    agent: Mapped[str | None] = mapped_column(String(60), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    # pending|running|retry_wait|waiting_approval|succeeded|failed|skipped
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    next_attempt_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True)
    started_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class ErrorQueueItem(Base):
    __tablename__ = "error_queue"
    id: Mapped[str] = _id("err")
    run_id: Mapped[str] = mapped_column(String(40))
    step_id: Mapped[str] = mapped_column(String(40))
    workflow: Mapped[str] = mapped_column(String(60))
    step_name: Mapped[str] = mapped_column(String(60))
    error: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(15), default="open")  # open | retried | dismissed
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    resolved_by: Mapped[str | None] = mapped_column(String(60), nullable=True)
