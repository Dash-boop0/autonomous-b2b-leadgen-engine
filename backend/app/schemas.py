"""Shared domain interfaces (canonical definitions; autonomous-b2b-revenue-ops mirrors these field names exactly).
Used for the export endpoints and for typed API responses of the shared entities."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

BuyerRole = Literal["economic_buyer", "champion", "user", "technical", "unknown"]
EmailStatus = Literal[
    "unknown",
    "syntax_valid",
    "syntax_invalid",
    "domain_has_mx",
    "provider_verified",
    "deliverable_confirmed",
    "bounced",
]
SourceType = Literal["csv", "api", "registry", "directory", "public_page", "licensed", "synthetic"]
SuppressionReason = Literal["opt_out", "bounce", "complaint", "manual", "legal"]


class _M(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Account(_M):
    id: str
    name: str
    domain: str | None
    industry: str | None
    employee_band: str | None
    revenue_band: str | None
    country: str | None
    region: str | None
    business_model: str | None
    source_ids: list[str]
    first_seen_at: datetime
    last_verified_at: datetime | None


class Contact(_M):
    id: str
    account_id: str
    full_name: str
    title: str | None
    seniority: str | None
    department: str | None
    buyer_role: BuyerRole
    email: str | None
    email_status: EmailStatus
    phone: str | None
    linkedin_url: str | None = None
    source_ids: list[str]
    consent_basis: str
    suppressed: bool
    suppression_reason: str | None


class SourceRecord(_M):
    id: str
    adapter: str
    source_type: SourceType
    url_or_ref: str
    retrieved_at: datetime
    terms_note: str
    confidence: float = Field(ge=0, le=1)
    raw_hash: str


class Evidence(_M):
    id: str
    entity_type: str
    entity_id: str
    claim: str
    excerpt: str
    source_id: str | None
    confidence: float
    created_by: Literal["agent", "human"]
    created_at: datetime


class HardDisqualifier(BaseModel):
    rule: str
    reason: str


class Criterion(BaseModel):
    name: str
    weight: float
    raw_value: object = None
    score_0_1: float
    contribution: float
    evidence_ids: list[str]
    missing: bool


class QualificationResult(_M):
    id: str
    entity: Literal["account", "contact", "lead"]
    model_version: str
    hard_disqualifiers: list[HardDisqualifier]
    criteria: list[Criterion]
    total_score_0_100: float
    tier: Literal["A", "B", "C", "disqualified"]
    explained_at: datetime


class Lead(_M):
    id: str
    account_id: str
    contact_id: str
    stage: str
    owner: str | None
    qualification_id: str | None
    suppression_checked_at: datetime | None


class ActorRef(BaseModel):
    actor_type: Literal["agent", "human"]
    actor_id: str


class Approval(BaseModel):
    id: str
    subject_type: str
    subject_id: str
    action: str
    requested_by: ActorRef
    status: Literal["pending", "approved", "rejected", "expired"]
    decided_by: str | None
    decided_at: datetime | None
    reason: str | None
    policy_id: str

    @classmethod
    def from_row(cls, a) -> Approval:
        return cls(
            id=a.id,
            subject_type=a.subject_type,
            subject_id=a.subject_id,
            action=a.action,
            requested_by=ActorRef(actor_type=a.requested_by_type, actor_id=a.requested_by_id),
            status=a.status,
            decided_by=a.decided_by,
            decided_at=a.decided_at,
            reason=a.reason,
            policy_id=a.policy_id,
        )


class AuditEvent(_M):
    id: str
    at: datetime
    actor_type: str
    actor_id: str
    action: str
    subject_type: str | None
    subject_id: str | None
    before: object = None
    after: object = None
    evidence_ids: list[str]
    run_id: str | None
    step_id: str | None


class Suppression(_M):
    id: str
    email_or_domain: str
    reason: SuppressionReason
    created_at: datetime
