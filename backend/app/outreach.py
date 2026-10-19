"""Deterministic outreach controls: suppression, approval-gated queueing, frequency caps, dry-run dispatch, follow-up
scheduling and cancellation. Nothing here calls an LLM, and nothing here can send a real email: the only sender is
the dry-run adapter, which writes to the send_queue/outbox table."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import approvals, audit
from .adapters.email_sender import get_sender
from .models import Campaign, Contact, Draft, Lead, SendQueueItem, Suppression
from .normalise import normalise_domain, normalise_email
from .security import Actor
from .system import assert_not_stopped, sim_now

SUPPRESSION_REASONS = {"opt_out", "bounce", "complaint", "manual", "legal"}


@dataclass
class SuppressionCheck:
    blocked: bool
    reason: str | None = None
    matched: str | None = None


# ---------------------------------------------------------------- suppression


def check_suppression(session: Session, contact: Contact) -> SuppressionCheck:
    if contact.suppressed:
        return SuppressionCheck(True, contact.suppression_reason or "manual", f"contact:{contact.id}")
    email = normalise_email(contact.email)
    if not email:
        return SuppressionCheck(True, "no_email", None)
    domain = email.split("@", 1)[1]
    hit = session.scalars(select(Suppression).where(Suppression.email_or_domain.in_([email, domain]))).first()
    if hit:
        return SuppressionCheck(True, hit.reason, hit.email_or_domain)
    return SuppressionCheck(False)


def add_suppression(session: Session, value: str, reason: str, actor: Actor, note: str = "") -> Suppression:
    if reason not in SUPPRESSION_REASONS:
        raise ValueError(f"reason must be one of {sorted(SUPPRESSION_REASONS)}")
    v = value.strip().lower()
    key = normalise_email(v) if "@" in v else normalise_domain(v)
    if not key:
        raise ValueError("email or domain required")
    sup = session.scalars(select(Suppression).where(Suppression.email_or_domain == key)).first()
    created = sup is None
    if created:
        sup = Suppression(email_or_domain=key, reason=reason, created_by=actor.label, note=note)
        session.add(sup)
        session.flush()
        audit.record(session, actor, "suppression.added", "suppression", sup.id, after={"value": key, "reason": reason})
    assert sup is not None
    # Propagate to matching contacts and cancel anything still queued for them.
    q = (
        select(Contact).where(Contact.email == key)
        if "@" in key
        else select(Contact).where(Contact.email.like(f"%@{key}"))
    )
    for c in session.scalars(q):
        if not c.suppressed:
            c.suppressed = True
            c.suppression_reason = sup.reason
            audit.record(session, actor, "contact.suppressed", "contact", c.id, after={"reason": sup.reason})
        cancel_pending_for_contact(session, c.id, f"suppressed:{sup.reason}", actor)
    return sup


# ---------------------------------------------------------------- queue / cancel


def cancel_pending_for_contact(session: Session, contact_id: str, reason: str, actor: Actor, min_step: int = 1) -> int:
    items = session.scalars(
        select(SendQueueItem).where(
            SendQueueItem.contact_id == contact_id, SendQueueItem.status == "queued", SendQueueItem.step_no >= min_step
        )
    ).all()
    for it in items:
        it.status = "cancelled"
        it.status_reason = reason
        d = session.get(Draft, it.draft_id)
        if d and d.status == "queued":
            d.status = "cancelled"
        audit.record(
            session, actor, "send.cancelled", "send_queue", it.id, after={"reason": reason, "step": it.step_no}
        )
    return len(items)


def _enqueue(
    session: Session, campaign: Campaign, draft: Draft, contact: Contact, when, actor: Actor
) -> tuple[SendQueueItem, bool]:
    """Idempotent insert keyed on campaign:contact:step. Returns (item, created)."""
    key = f"{campaign.id}:{contact.id}:{draft.step_no}"
    existing = session.scalars(select(SendQueueItem).where(SendQueueItem.idempotency_key == key)).first()
    if existing:
        return existing, False
    chk = check_suppression(session, contact)
    item = SendQueueItem(
        campaign_id=campaign.id,
        draft_id=draft.id,
        contact_id=contact.id,
        lead_id=draft.lead_id,
        step_no=draft.step_no,
        to_email=contact.email or "",
        scheduled_for=when,
        idempotency_key=key,
        status="blocked" if chk.blocked else "queued",
        status_reason=f"suppressed:{chk.reason}" if chk.blocked else None,
    )
    try:
        with session.begin_nested():
            session.add(item)
            session.flush()
    except IntegrityError:  # concurrent duplicate: someone else inserted it first
        return session.scalars(select(SendQueueItem).where(SendQueueItem.idempotency_key == key)).one(), False
    if not chk.blocked:
        draft.status = "queued"
    audit.record(
        session,
        actor,
        "send.blocked" if chk.blocked else "send.queued",
        "send_queue",
        item.id,
        after={"step": draft.step_no, "reason": item.status_reason},
        severity="warn" if chk.blocked else "info",
    )
    return item, True


def queue_campaign(session: Session, campaign: Campaign, actor: Actor) -> dict:
    """Queue step-1 messages for every approved, policy-passing draft. Requires a human campaign approval."""
    assert_not_stopped(session)
    approvals.assert_approved(session, "campaign", campaign.id, "campaign.activate")
    if campaign.status not in ("approved", "active"):
        raise approvals.ApprovalError(f"Campaign status is {campaign.status}; it must be approved first")
    now = sim_now(session)
    created = blocked = skipped = 0
    drafts = session.scalars(
        select(Draft).where(Draft.campaign_id == campaign.id, Draft.step_no == 1, Draft.status.in_(["draft", "queued"]))
    ).all()
    for d in drafts:
        if not d.policy_passed:
            skipped += 1
            continue
        contact = session.get(Contact, d.contact_id)
        if contact is None:
            continue
        item, was_new = _enqueue(session, campaign, d, contact, now, actor)
        if was_new:
            created += item.status == "queued"
            blocked += item.status == "blocked"
    campaign.status = "active"
    return {"queued": created, "blocked": blocked, "skipped_policy": skipped}


def _contact_has_stopped(session: Session, contact_id: str) -> str | None:
    lead = session.scalars(select(Lead).where(Lead.contact_id == contact_id)).first()
    if lead and lead.stage in ("replied", "interested", "not_interested", "unsubscribed", "bounced", "disqualified"):
        return f"lead_stage:{lead.stage}"
    return None


def schedule_followup(session: Session, sent: SendQueueItem, actor: Actor) -> SendQueueItem | None:
    campaign = session.get(Campaign, sent.campaign_id)
    assert campaign is not None
    nxt = next((s for s in campaign.sequence if s["step"] == sent.step_no + 1), None)
    if not nxt:
        return None
    draft = session.scalars(
        select(Draft).where(
            Draft.campaign_id == campaign.id, Draft.contact_id == sent.contact_id, Draft.step_no == nxt["step"]
        )
    ).first()
    if not draft or draft.status == "rejected" or not draft.policy_passed:
        return None
    if _contact_has_stopped(session, sent.contact_id):
        return None
    contact = session.get(Contact, sent.contact_id)
    assert contact is not None and sent.sent_at is not None
    item, _ = _enqueue(session, campaign, draft, contact, sent.sent_at + timedelta(days=int(nxt["delay_days"])), actor)
    return item


def dispatch_due(session: Session, campaign: Campaign, actor: Actor, limit: int = 500) -> dict:
    """Dry-run 'send' of every due queued message, re-checking stop, approval, suppression and caps at send time."""
    assert_not_stopped(session)
    approvals.assert_approved(session, "campaign", campaign.id, "campaign.activate")
    if campaign.status not in ("active",):
        return {"sent": 0, "deferred": 0, "blocked": 0, "note": f"campaign is {campaign.status}"}
    now = sim_now(session)
    cap = campaign.frequency_cap or {}
    per_contact_7d = int(cap.get("max_per_contact_per_7d", 2))
    per_day = int(cap.get("max_sends_per_day", 50))
    sender = get_sender()
    sent = deferred = blocked = 0
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    sent_today = (
        session.scalar(
            select(func.count())
            .select_from(SendQueueItem)
            .where(
                SendQueueItem.campaign_id == campaign.id,
                SendQueueItem.status == "sent_simulated",
                SendQueueItem.sent_at >= day_start,
            )
        )
        or 0
    )
    due = session.scalars(
        select(SendQueueItem)
        .where(
            SendQueueItem.campaign_id == campaign.id,
            SendQueueItem.status == "queued",
            SendQueueItem.scheduled_for <= now,
        )
        .order_by(SendQueueItem.scheduled_for, SendQueueItem.id)
        .limit(limit)
    ).all()
    for it in due:
        contact = session.get(Contact, it.contact_id)
        assert contact is not None
        chk = check_suppression(session, contact)
        stop = _contact_has_stopped(session, contact.id) if it.step_no > 1 else None
        if chk.blocked or stop:
            it.status = "blocked" if chk.blocked else "cancelled"
            it.status_reason = f"suppressed:{chk.reason}" if chk.blocked else stop
            blocked += 1
            audit.record(
                session,
                actor,
                "send.blocked_at_dispatch",
                "send_queue",
                it.id,
                after={"reason": it.status_reason},
                severity="warn",
            )
            continue
        if sent_today >= per_day:
            deferred += 1
            continue
        recent = (
            session.scalar(
                select(func.count())
                .select_from(SendQueueItem)
                .where(
                    SendQueueItem.contact_id == contact.id,
                    SendQueueItem.status == "sent_simulated",
                    SendQueueItem.sent_at >= now - timedelta(days=7),
                )
            )
            or 0
        )
        if recent >= per_contact_7d:
            it.scheduled_for = now + timedelta(days=1)
            it.status_reason = "deferred:frequency_cap"
            deferred += 1
            audit.record(session, actor, "send.deferred", "send_queue", it.id, after={"reason": "frequency_cap"})
            continue
        draft = session.get(Draft, it.draft_id)
        assert draft is not None
        it.provider_message_id = sender.send(to=it.to_email, subject=draft.subject, body=draft.body, ref=it.id)
        it.status = "sent_simulated"
        it.sent_at = now
        it.mode = sender.mode
        draft.status = "sent"
        sent += 1
        sent_today += 1
        lead = session.get(Lead, it.lead_id)
        if lead and lead.stage in ("qualified", "new"):
            lead.stage = "in_campaign"
        audit.record(
            session,
            actor,
            "send.dispatched_dry_run",
            "send_queue",
            it.id,
            after={"to": it.to_email, "step": it.step_no, "provider_message_id": it.provider_message_id},
        )
        session.flush()
        schedule_followup(session, it, actor)
    return {"sent": sent, "deferred": deferred, "blocked": blocked}
