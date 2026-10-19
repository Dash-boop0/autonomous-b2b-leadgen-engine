"""Campaign manager, send queue/outbox, simulated events, reply triage, analytics and mock-CRM outbox."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..adapters import events as events_adapter
from ..agents.governance import compute_campaign_analytics
from ..db import get_session
from ..models import (
    Account,
    Campaign,
    Contact,
    CrmOutbox,
    Draft,
    Evidence,
    ICPVersion,
    MessageEvent,
    Reply,
    ResearchArtifact,
    SendQueueItem,
    WorkflowRun,
)
from ..security import Actor, require
from ..workflows import engine as wf
from .flows import run_out
from .util import to_dict

router = APIRouter(prefix="/api")


class CampaignIn(BaseModel):
    name: str = Field(min_length=3, max_length=200)
    icp_version_id: str
    tiers: list[str] = Field(default_factory=lambda: ["A", "B"])
    industries: list[str] = Field(default_factory=list)
    follow_up_delay_days: int = Field(3, ge=1, le=30)
    max_per_contact_per_7d: int = Field(2, ge=1, le=5)
    max_sends_per_day: int = Field(50, ge=1, le=500)


def _campaign(session: Session, cid: str) -> Campaign:
    c = session.get(Campaign, cid)
    if c is None:
        raise HTTPException(404, "campaign not found")
    return c


def _runs(session: Session, cid: str) -> list[dict]:
    rs = [
        r
        for r in session.scalars(select(WorkflowRun).order_by(WorkflowRun.created_at.desc()))
        if r.input.get("campaign_id") == cid
    ]
    return [
        {"id": r.id, "workflow": r.workflow, "status": r.status, "created_at": r.created_at.isoformat()} for r in rs
    ]


@router.get("/campaigns")
def list_campaigns(session: Session = Depends(get_session), _: Actor = Depends(require("read"))) -> list[dict]:
    out = []
    for c in session.scalars(select(Campaign).order_by(Campaign.created_at.desc())):
        q = session.scalars(select(SendQueueItem).where(SendQueueItem.campaign_id == c.id)).all()
        counts: dict[str, int] = {}
        for it in q:
            counts[it.status] = counts.get(it.status, 0) + 1
        n = len(session.scalars(select(Draft.id).where(Draft.campaign_id == c.id)).all())
        out.append({**to_dict(c), "drafts": n, "queue": counts})
    return out


@router.post("/campaigns")
def create_campaign(
    body: CampaignIn, session: Session = Depends(get_session), actor: Actor = Depends(require("campaigns.manage"))
) -> dict:
    icp = session.get(ICPVersion, body.icp_version_id)
    if icp is None or icp.status != "approved":
        raise HTTPException(409, "Campaigns need an approved ICP version")
    c = Campaign(
        name=body.name,
        icp_version_id=icp.id,
        created_by=actor.actor_id,
        segment_filter={"tiers": body.tiers, "industries": body.industries},
        sequence=[
            {"step": 1, "delay_days": 0, "template": "intro"},
            {"step": 2, "delay_days": body.follow_up_delay_days, "template": "follow_up"},
        ],
        frequency_cap={
            "max_per_contact_per_7d": body.max_per_contact_per_7d,
            "max_sends_per_day": body.max_sends_per_day,
        },
        schedule={"mode": "dry_run", "timezone": "Europe/London", "send_window": "09:00-17:00 (simulated)"},
    )
    session.add(c)
    session.flush()
    audit.record(session, actor, "campaign.created", "campaign", c.id, after=to_dict(c))
    session.commit()
    run = wf.start_run(session, "lead_to_campaign", {"campaign_id": c.id}, actor, idempotency_key=f"l2c:{c.id}")
    res = wf.advance(session, run.id)
    return {"campaign": to_dict(c), "run": run_out(session, session.get(WorkflowRun, run.id)), "advance": res}


@router.get("/campaigns/{cid}")
def campaign_detail(cid: str, session: Session = Depends(get_session), _: Actor = Depends(require("read"))) -> dict:
    c = _campaign(session, cid)
    drafts = []
    for d in session.scalars(select(Draft).where(Draft.campaign_id == c.id).order_by(Draft.contact_id, Draft.step_no)):
        contact = session.get(Contact, d.contact_id)
        acc = session.get(Account, contact.account_id) if contact else None
        evs = [
            {"id": e.id, "claim": e.claim, "excerpt": e.excerpt, "source_id": e.source_id, "confidence": e.confidence}
            for e in (session.get(Evidence, i) for i in d.evidence_ids)
            if e
        ]
        drafts.append(
            {
                **to_dict(d),
                "contact_name": contact.full_name if contact else None,
                "contact_email": contact.email if contact else None,
                "account_name": acc.name if acc else None,
                "evidence": evs,
            }
        )
    queue = [
        to_dict(q)
        for q in session.scalars(
            select(SendQueueItem)
            .where(SendQueueItem.campaign_id == c.id)
            .order_by(SendQueueItem.scheduled_for, SendQueueItem.step_no)
        )
    ]
    return {"campaign": to_dict(c), "drafts": drafts, "queue": queue, "runs": _runs(session, c.id)}


def _start(session: Session, name: str, inp: dict, actor: Actor) -> dict:
    run = wf.start_run(session, name, inp, actor)
    res = wf.advance(session, run.id)
    return {"advance": res, "run": run_out(session, session.get(WorkflowRun, run.id))}


@router.post("/campaigns/{cid}/conversation-cycle")
def conversation_cycle(
    cid: str, session: Session = Depends(get_session), actor: Actor = Depends(require("events.simulate"))
) -> dict:
    """Run the Campaign-to-conversation workflow: dry-run dispatch of due items, simulated inbox events,
    classification, suppression, follow-up cancellation, response drafts, mock-CRM update."""
    c = _campaign(session, cid)
    if c.status not in ("active", "paused"):
        raise HTTPException(409, f"Campaign is {c.status}; it must be approved and queued first")
    return _start(session, "campaign_to_conversation", {"campaign_id": c.id}, actor)


class PauseIn(BaseModel):
    paused: bool


@router.post("/campaigns/{cid}/pause")
def pause_campaign(
    cid: str,
    body: PauseIn,
    session: Session = Depends(get_session),
    actor: Actor = Depends(require("campaigns.manage")),
) -> dict:
    c = _campaign(session, cid)
    if body.paused and c.status == "active":
        c.status = "paused"
    elif not body.paused and c.status == "paused":
        c.status = "active"
    audit.record(session, actor, "campaign.paused" if body.paused else "campaign.resumed", "campaign", c.id)
    session.commit()
    return to_dict(c)


class EventIn(BaseModel):
    provider_event_id: str = Field(min_length=1, max_length=120)
    event_type: str
    send_id: str
    body: str | None = None


@router.post("/events")
def inject_event(
    body: EventIn, session: Session = Depends(get_session), actor: Actor = Depends(require("events.simulate"))
) -> dict:
    """Inject a (simulated) provider event, e.g. to demonstrate duplicate-event protection."""
    s = session.get(SendQueueItem, body.send_id)
    if s is None:
        raise HTTPException(404, "send not found")
    try:
        ev, created = events_adapter.ingest(
            session,
            provider="manual-simulator",
            provider_event_id=body.provider_event_id,
            event_type=body.event_type,
            send_id=s.id,
            contact_id=s.contact_id,
            payload={"body": body.body} if body.body else {},
        )
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    audit.record(
        session,
        actor,
        "event.injected" if created else "event.duplicate_ignored",
        "message_event",
        ev.id,
        after={"provider_event_id": body.provider_event_id, "type": body.event_type},
    )
    session.commit()
    return {"event_id": ev.id, "created": created, "duplicate": not created}


@router.get("/events")
def list_events(
    campaign_id: str | None = None, session: Session = Depends(get_session), _: Actor = Depends(require("read"))
) -> list[dict]:
    q = select(MessageEvent).order_by(MessageEvent.received_at.desc()).limit(500)
    rows = session.scalars(q).all()
    if campaign_id:
        ids = set(session.scalars(select(SendQueueItem.id).where(SendQueueItem.campaign_id == campaign_id)))
        rows = [r for r in rows if r.send_id in ids]
    return [to_dict(r) for r in rows]


# ---------------------------------------------------------------- replies


@router.get("/replies")
def replies(
    status: str | None = None, session: Session = Depends(get_session), _: Actor = Depends(require("read"))
) -> list[dict]:
    q = select(Reply).order_by(Reply.created_at.desc())
    if status:
        q = q.where(Reply.status == status)
    out = []
    for r in session.scalars(q):
        c = session.get(Contact, r.contact_id)
        acc = session.get(Account, c.account_id) if c else None
        out.append(
            {
                **to_dict(r),
                "contact_name": c.full_name if c else None,
                "contact_email": c.email if c else None,
                "account_name": acc.name if acc else None,
                "suppressed": bool(c and c.suppressed),
            }
        )
    return out


class TriageIn(BaseModel):
    classification: str | None = None
    status: str = Field("resolved", pattern="^(resolved|needs_review|escalated)$")
    note: str | None = None


@router.post("/replies/{rid}/triage")
def triage(
    rid: str, body: TriageIn, session: Session = Depends(get_session), actor: Actor = Depends(require("replies.triage"))
) -> dict:
    r = session.get(Reply, rid)
    if r is None:
        raise HTTPException(404, "reply not found")
    before = {"classification": r.classification, "status": r.status}
    if body.classification:
        r.classification = body.classification
        r.classifier = f"human:{actor.actor_id}"
    r.status = body.status
    r.triaged_by = actor.actor_id
    if body.classification == "unsubscribe":
        from ..outreach import add_suppression

        c = session.get(Contact, r.contact_id)
        if c and c.email:
            add_suppression(session, c.email, "opt_out", actor, note=f"triage of reply {r.id}")
    audit.record(
        session,
        actor,
        "reply.triaged",
        "reply",
        r.id,
        before=before,
        after={"classification": r.classification, "status": r.status, "note": body.note},
    )
    session.commit()
    return to_dict(r)


# ---------------------------------------------------------------- analytics


@router.get("/analytics/campaigns/{cid}")
def analytics(cid: str, session: Session = Depends(get_session), _: Actor = Depends(require("read"))) -> dict:
    _campaign(session, cid)
    a = compute_campaign_analytics(session, cid)
    recs = session.scalars(select(ResearchArtifact).where(ResearchArtifact.kind == "icp_recommendation")).all()
    proposals = [
        to_dict(i)
        for i in session.scalars(
            select(ICPVersion).where(ICPVersion.parent_version_id.isnot(None)).order_by(ICPVersion.version.desc())
        )
    ]
    last = next(
        (
            r
            for r in session.scalars(
                select(WorkflowRun)
                .where(WorkflowRun.workflow == "performance_to_improvement")
                .order_by(WorkflowRun.created_at.desc())
            )
            if r.input.get("campaign_id") == cid
        ),
        None,
    )
    return {
        **a,
        "recommendation_run": run_out(session, last) if last else None,
        "recommendations": (last.context.get("recommend", {}).get("recommendations", []) if last else []),
        "narrative": last.context.get("analytics", {}).get("narrative") if last else None,
        "proposals": proposals,
        "artifacts": [to_dict(r) for r in recs],
    }


class ImproveIn(BaseModel):
    icp_version_id: str | None = None


@router.post("/analytics/campaigns/{cid}/improve")
def improve(
    cid: str, body: ImproveIn, session: Session = Depends(get_session), actor: Actor = Depends(require("workflows.run"))
) -> dict:
    c = _campaign(session, cid)
    return _start(
        session,
        "performance_to_improvement",
        {"campaign_id": c.id, "icp_version_id": body.icp_version_id or c.icp_version_id},
        actor,
    )


@router.get("/crm/outbox")
def crm_outbox(session: Session = Depends(get_session), _: Actor = Depends(require("read"))) -> list[dict]:
    return [to_dict(x) for x in session.scalars(select(CrmOutbox).order_by(CrmOutbox.created_at.desc()))]
