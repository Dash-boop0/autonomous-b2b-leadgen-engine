"""Campaign & execution agents: Campaign Planning, Personalisation, Outreach Operations, Inbox & Reply Classification,
Follow-up (cancellation + knowledge-grounded responses)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from ..models import (
    Account,
    Campaign,
    Contact,
    Draft,
    Evidence,
    KnowledgePassage,
    Lead,
    MessageEvent,
    QualificationResult,
    Reply,
    SendQueueItem,
)
from .base import AgentOutput, BaseAgent

SENDER = "Sam Rivers, Northwind Analytics (fictional)"
FOOTER = (
    "\n\n--\nNorthwind Analytics (fictional) - 1 Example Street, Example City. "
    "Reply 'unsubscribe' and we will not contact you again."
)
ANGLES = {
    "Logistics": "warehouse and labour capacity planning",
    "Wholesale Distribution": "fewer stockouts on fast movers",
    "Manufacturing": "production plans from late customer forecasts",
    "Food & Beverage": "less waste on perishables",
}

# ---------------------------------------------------------------- Campaign Planning


class PlanningInput(BaseModel):
    campaign_id: str


class PlannedSegment(BaseModel):
    name: str
    angle: str
    lead_ids: list[str]


class PlanningOutput(AgentOutput):
    segments: list[PlannedSegment] = Field(default_factory=list)
    selected_lead_ids: list[str] = Field(default_factory=list)
    excluded: dict[str, int] = Field(default_factory=dict)


class CampaignPlanningAgent(BaseAgent):
    name = "campaign_planning"
    group = "Campaign & execution"
    description = (
        "Segments qualified, suppression-checked leads and sets the angle, sequence and caps for each segment."
    )
    tools = ()
    input_model = PlanningInput
    output_model = PlanningOutput
    completion_criteria = "At least one lead selected and every selected lead was suppression-checked."

    def run(self, inp: PlanningInput) -> PlanningOutput:
        camp = self.session.get(Campaign, inp.campaign_id)
        assert camp is not None
        tiers = set(camp.segment_filter.get("tiers", ["A", "B"]))
        industries = set(camp.segment_filter.get("industries", []))
        out = PlanningOutput(confidence=0.85)
        by_seg: dict[str, list[str]] = {}
        for lead in self.session.scalars(select(Lead).order_by(Lead.id)):
            if lead.stage != "qualified":
                out.excluded[f"stage:{lead.stage}"] = out.excluded.get(f"stage:{lead.stage}", 0) + 1
                continue
            if lead.suppression_checked_at is None:
                out.excluded["not_suppression_checked"] = out.excluded.get("not_suppression_checked", 0) + 1
                continue
            qr = self.session.get(QualificationResult, lead.qualification_id) if lead.qualification_id else None
            if not qr or qr.tier not in tiers:
                out.excluded["tier"] = out.excluded.get("tier", 0) + 1
                continue
            acc = self.session.get(Account, lead.account_id)
            ind = acc.industry if acc else "Unknown"
            if industries and ind not in industries:
                out.excluded["industry_filter"] = out.excluded.get("industry_filter", 0) + 1
                continue
            lead.segment = ind
            by_seg.setdefault(ind or "Unknown", []).append(lead.id)
        for seg, ids in sorted(by_seg.items()):
            out.segments.append(PlannedSegment(name=seg, angle=ANGLES.get(seg, "demand planning"), lead_ids=ids))
            out.selected_lead_ids += ids
        camp.strategy = {
            "segments": [s.model_dump() for s in out.segments],
            "excluded": out.excluded,
            "planned_by": f"agent:{self.name}",
        }
        if not camp.sequence:
            camp.sequence = [
                {"step": 1, "delay_days": 0, "template": "intro"},
                {"step": 2, "delay_days": 3, "template": "follow_up"},
            ]
        if not camp.frequency_cap:
            camp.frequency_cap = {"max_per_contact_per_7d": 2, "max_sends_per_day": 50}
        return out

    def check_completion(self, out: PlanningOutput) -> list[str]:
        return [] if out.selected_lead_ids else ["no qualified, suppression-checked leads match the campaign filter"]


# ---------------------------------------------------------------- Personalisation


class PersonalisationInput(BaseModel):
    campaign_id: str
    lead_ids: list[str]


class DraftSummary(BaseModel):
    draft_id: str
    contact_id: str
    step_no: int
    personalisation_level: str
    evidence_ids: list[str]


class PersonalisationOutput(AgentOutput):
    drafts: list[DraftSummary] = Field(default_factory=list)


class PersonalisationAgent(BaseAgent):
    name = "personalisation"
    group = "Campaign & execution"
    description = (
        "Drafts each sequence step. Personal hooks come only from stored evidence (cited by id); with no "
        "usable evidence it falls back to a generic draft and says so."
    )
    tools = ("llm.generate",)
    input_model = PersonalisationInput
    output_model = PersonalisationOutput
    confidence_threshold = 0.5
    completion_criteria = "One draft per lead per step; evidence-grounded drafts cite at least one evidence id."

    def run(self, inp: PersonalisationInput) -> PersonalisationOutput:
        camp = self.session.get(Campaign, inp.campaign_id)
        assert camp is not None
        out = PersonalisationOutput()
        vp = self.session.get(KnowledgePassage, "northwind-approved#product-overview")
        value_prop = vp.text if vp else "Northwind Forecast produces weekly SKU-level demand forecasts."
        grounded = 0
        for lid in inp.lead_ids:
            lead = self.session.get(Lead, lid)
            if lead is None:
                continue
            contact = self.session.get(Contact, lead.contact_id)
            acc = self.session.get(Account, lead.account_id)
            assert contact is not None and acc is not None
            evs = self.session.scalars(
                select(Evidence)
                .where(Evidence.entity_id == acc.id, Evidence.kind == "signal", Evidence.confidence >= 0.6)
                .order_by(Evidence.confidence.desc(), Evidence.created_at.desc())
            ).all()[:1]
            prev_subject = None
            for st in sorted(camp.sequence, key=lambda s: s["step"]):
                existing = self.session.scalars(
                    select(Draft).where(
                        Draft.campaign_id == camp.id, Draft.contact_id == contact.id, Draft.step_no == st["step"]
                    )
                ).first()
                if existing:
                    prev_subject = existing.subject
                    out.drafts.append(
                        DraftSummary(
                            draft_id=existing.id,
                            contact_id=contact.id,
                            step_no=st["step"],
                            personalisation_level=existing.personalisation_level,
                            evidence_ids=existing.evidence_ids,
                        )
                    )
                    continue
                ev_payload = [{"id": e.id, "claim": e.claim.split(": ", 1)[-1]} for e in evs] if st["step"] == 1 else []
                r = self.llm(
                    "draft_email",
                    {
                        "step": st["step"],
                        "first_name": contact.full_name.split()[0],
                        "company": acc.name,
                        "industry": acc.industry,
                        "angle": ANGLES.get(acc.industry or "", "demand planning"),
                        "evidence": ev_payload,
                        "value_prop": value_prop,
                        "sender": SENDER,
                        "prev_subject": prev_subject or "",
                    },
                )
                level = "evidence_grounded" if ev_payload else "generic"
                d = Draft(
                    campaign_id=camp.id,
                    lead_id=lead.id,
                    contact_id=contact.id,
                    step_no=st["step"],
                    subject=r.data.get("subject", "Demand planning"),
                    body=r.text + FOOTER,
                    evidence_ids=[e["id"] for e in ev_payload],
                    personalisation_level=level,
                    llm_provider=r.provider,
                    status="draft",
                )
                self.session.add(d)
                self.session.flush()
                prev_subject = d.subject
                grounded += level == "evidence_grounded"
                out.drafts.append(
                    DraftSummary(
                        draft_id=d.id,
                        contact_id=contact.id,
                        step_no=d.step_no,
                        personalisation_level=level,
                        evidence_ids=d.evidence_ids,
                    )
                )
        firsts = [d for d in out.drafts if d.step_no == 1]
        out.confidence = round(
            0.5 + 0.5 * (sum(d.personalisation_level == "evidence_grounded" for d in firsts) / max(1, len(firsts))), 2
        )
        out.notes.append(f"{grounded} new evidence-grounded draft(s); generic drafts are labelled as such")
        return out

    def check_completion(self, out: PersonalisationOutput) -> list[str]:
        bad = [d for d in out.drafts if d.personalisation_level == "evidence_grounded" and not d.evidence_ids]
        return ["grounded draft without evidence"] if bad else []


# ---------------------------------------------------------------- Outreach Operations


class OutreachInput(BaseModel):
    campaign_id: str
    mode: Literal["queue", "dispatch", "crm_sync"]


class OutreachOutput(AgentOutput):
    result: dict = Field(default_factory=dict)


class OutreachOperationsAgent(BaseAgent):
    name = "outreach_operations"
    group = "Campaign & execution"
    description = (
        "Queues approved drafts and runs the dry-run dispatcher. Approval, suppression, caps and the "
        "emergency stop are enforced by deterministic code it calls, not by the agent. Syncs lead stages "
        "to the mock CRM."
    )
    tools = ("outreach.queue", "outreach.dispatch", "crm.upsert")
    input_model = OutreachInput
    output_model = OutreachOutput
    completion_criteria = "Queue/dispatch result recorded; no item is sent without approval and suppression checks."

    def run(self, inp: OutreachInput) -> OutreachOutput:
        camp = self.session.get(Campaign, inp.campaign_id)
        assert camp is not None
        if inp.mode == "queue":
            return OutreachOutput(result=self.tool("outreach.queue", campaign=camp), confidence=1.0)
        if inp.mode == "dispatch":
            return OutreachOutput(result=self.tool("outreach.dispatch", campaign=camp), confidence=1.0)
        pushed = 0
        lead_ids = {
            s.lead_id for s in self.session.scalars(select(SendQueueItem).where(SendQueueItem.campaign_id == camp.id))
        }
        for lid in sorted(lead_ids):
            lead = self.session.get(Lead, lid)
            if lead is None:
                continue
            contact = self.session.get(Contact, lead.contact_id)
            pushed += self.tool(
                "crm.upsert",
                entity_type="lead",
                entity_id=lead.id,
                operation="upsert_stage",
                payload={"stage": lead.stage, "email": contact.email if contact else None, "campaign_id": camp.id},
                key=f"crm:lead:{lead.id}:{lead.stage}",
            )
        return OutreachOutput(result={"crm_updates": pushed, "adapter": "mock-crm (crm_outbox table)"}, confidence=1.0)


# ---------------------------------------------------------------- Inbox & Reply Classification


class ClassificationInput(BaseModel):
    campaign_id: str


class ClassifiedReply(BaseModel):
    reply_id: str
    classification: str
    confidence: float
    needs_human: bool
    status: str


class ClassificationOutput(AgentOutput):
    replies: list[ClassifiedReply] = Field(default_factory=list)


ESCALATE = {
    "question_pricing": "Pricing questions are answered by a human (policy)",
    "question_legal": "Legal/contract questions are answered by a human (policy)",
}


class ReplyClassificationAgent(BaseAgent):
    name = "reply_classification"
    group = "Campaign & execution"
    description = (
        "Classifies inbound replies (interested, not interested, unsubscribe, OOO, referral, pricing/legal/"
        "product question, unknown). Low confidence goes to human triage; pricing/legal always escalate."
    )
    tools = ("llm.generate",)
    input_model = ClassificationInput
    output_model = ClassificationOutput
    confidence_threshold = 0.6
    completion_criteria = "Every reply event has exactly one Reply row with a label, confidence and routing."

    def run(self, inp: ClassificationInput) -> ClassificationOutput:
        out = ClassificationOutput(confidence=1.0)
        sends = {
            s.id: s
            for s in self.session.scalars(select(SendQueueItem).where(SendQueueItem.campaign_id == inp.campaign_id))
        }
        evs = self.session.scalars(
            select(MessageEvent).where(MessageEvent.event_type == "replied", MessageEvent.send_id.in_(list(sends)))
        ).all()
        for ev in evs:
            if self.session.scalars(select(Reply).where(Reply.event_id == ev.id)).first():
                continue  # idempotent
            body = ev.payload.get("body", "")
            r = self.llm("classify_reply", {"body": body})
            label, conf = r.data.get("label", r.text), float(r.confidence or 0)
            needs_human = conf < self.confidence_threshold or label == "unknown"
            status = "escalated" if label in ESCALATE else "needs_review" if needs_human else "auto_handled"
            rep = Reply(
                event_id=ev.id,
                contact_id=ev.contact_id or "",
                campaign_id=inp.campaign_id,
                body=body,
                classification=label,
                confidence=conf,
                classifier=r.provider,
                needs_human=needs_human or label in ESCALATE,
                status=status,
                escalation_reason=ESCALATE.get(label),
            )
            self.session.add(rep)
            self.session.flush()
            out.replies.append(
                ClassifiedReply(
                    reply_id=rep.id, classification=label, confidence=conf, needs_human=rep.needs_human, status=status
                )
            )
        if out.replies:
            out.confidence = round(min(r.confidence for r in out.replies), 2)
            if out.confidence < self.confidence_threshold:
                out.notes.append("At least one reply was routed to human triage")
                out.confidence = self.confidence_threshold  # routed, so the step itself is complete
        return out


# ---------------------------------------------------------------- Follow-up


class FollowUpInput(BaseModel):
    campaign_id: str
    mode: Literal["cancel", "respond"]


class FollowUpOutput(AgentOutput):
    cancelled: int = 0
    stage_updates: dict[str, str] = Field(default_factory=dict)
    responses: list[dict] = Field(default_factory=list)


STAGE_FOR = {
    "interested": "interested",
    "referral": "interested",
    "not_interested": "not_interested",
    "unsubscribe": "unsubscribed",
    "question_pricing": "replied",
    "question_legal": "replied",
    "question_product": "replied",
    "unknown": "replied",
}


class FollowUpAgent(BaseAgent):
    name = "follow_up"
    group = "Campaign & execution"
    description = (
        "Cancels queued follow-ups after replies, unsubscribes, bounces or disqualification, updates lead "
        "stages, and drafts knowledge-grounded responses that cite approved passages or abstain/escalate."
    )
    tools = ("outreach.cancel_followups", "knowledge.search", "llm.generate")
    input_model = FollowUpInput
    output_model = FollowUpOutput
    confidence_threshold = 0.5
    completion_criteria = (
        "No queued follow-up remains for a contact who replied, unsubscribed, bounced or was "
        "disqualified; every question reply has a cited draft, an abstention or an escalation."
    )

    def run(self, inp: FollowUpInput) -> FollowUpOutput:
        out = FollowUpOutput(confidence=0.9)
        sends = self.session.scalars(select(SendQueueItem).where(SendQueueItem.campaign_id == inp.campaign_id)).all()
        contact_ids = sorted({s.contact_id for s in sends})
        if inp.mode == "cancel":
            for cid in contact_ids:
                lead = self.session.scalars(select(Lead).where(Lead.contact_id == cid)).first()
                reasons = []
                reps = self.session.scalars(select(Reply).where(Reply.contact_id == cid)).all()
                human = [r for r in reps if r.classification not in ("out_of_office",)]
                if human:
                    reasons.append(f"replied:{human[-1].classification}")
                    if lead and lead.stage not in ("unsubscribed", "bounced"):
                        new = STAGE_FOR.get(human[-1].classification, "replied")
                        if lead.stage != new:
                            lead.stage = new
                            out.stage_updates[lead.id] = new
                evtypes = {
                    e.event_type
                    for e in self.session.scalars(select(MessageEvent).where(MessageEvent.contact_id == cid))
                }
                for et, stage in (
                    ("bounced", "bounced"),
                    ("unsubscribed", "unsubscribed"),
                    ("complaint", "unsubscribed"),
                ):
                    if et in evtypes:
                        reasons.append(et)
                        if lead and lead.stage != stage:
                            lead.stage = stage
                            out.stage_updates[lead.id] = stage
                if lead and lead.stage in ("disqualified", "suppressed"):
                    reasons.append(f"lead_stage:{lead.stage}")
                if reasons:
                    out.cancelled += self.tool("outreach.cancel_followups", contact_id=cid, reason="; ".join(reasons))
            return out
        # respond: knowledge-grounded drafts for question replies, abstain or escalate otherwise
        for rep in self.session.scalars(
            select(Reply).where(Reply.campaign_id == inp.campaign_id, Reply.response_status == "none")
        ):
            contact = self.session.get(Contact, rep.contact_id)
            first = contact.full_name.split()[0] if contact else "there"
            if rep.classification in ESCALATE:
                rep.response_status = "escalated"
                rep.status = "escalated"
                rep.escalation_reason = ESCALATE[rep.classification]
            elif rep.classification in ("interested", "referral"):
                rep.response_status = "escalated"
                rep.status = "escalated"
                rep.escalation_reason = "Positive reply: a human arranges the meeting (agents make no commitments)"
            elif rep.classification == "question_product":
                hits = self.tool("knowledge.search", question=rep.body)
                if not hits:
                    rep.response_status = "abstained"
                    rep.status = "needs_review"
                    rep.needs_human = True
                    rep.escalation_reason = "No approved passage answers this; abstained"
                else:
                    r = self.llm("draft_response", {"first_name": first, "passages": hits[:2], "sender": SENDER})
                    rep.suggested_response = r.text
                    rep.response_citations = [
                        {"id": h["id"], "doc": h["doc"], "section": h["section"], "version": h["version"]}
                        for h in hits[:2]
                    ]
                    rep.response_status = "drafted"
                    rep.needs_human = True  # drafts are never sent automatically
                    if rep.status == "auto_handled":
                        rep.status = "needs_review"
            else:
                continue
            out.responses.append(
                {
                    "reply_id": rep.id,
                    "classification": rep.classification,
                    "response_status": rep.response_status,
                    "citations": rep.response_citations,
                }
            )
        return out
