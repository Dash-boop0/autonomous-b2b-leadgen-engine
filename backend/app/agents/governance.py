"""Measurement & governance agents: Performance Analytics, ICP Feedback, Compliance & Data Governance,
Workflow Supervisor."""

from __future__ import annotations

import copy
import re
from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .. import metrics
from ..models import (
    Account,
    Approval,
    Contact,
    Draft,
    ErrorQueueItem,
    Evidence,
    ICPVersion,
    Lead,
    MessageEvent,
    QualificationResult,
    Reply,
    SendQueueItem,
    SourceAdapter,
    SourceRecord,
    WorkflowRun,
    WorkflowStep,
    utcnow,
)
from .base import AgentOutput, BaseAgent

# ---------------------------------------------------------------- analytics data


def campaign_rows(session, campaign_id: str) -> tuple[list[dict], list[dict], list[dict]]:
    sends = [
        {"id": s.id, "contact_id": s.contact_id, "step_no": s.step_no, "status": s.status}
        for s in session.scalars(select(SendQueueItem).where(SendQueueItem.campaign_id == campaign_id))
    ]
    ids = [s["id"] for s in sends]
    events = (
        [
            {"send_id": e.send_id, "contact_id": e.contact_id, "event_type": e.event_type}
            for e in session.scalars(select(MessageEvent).where(MessageEvent.send_id.in_(ids)))
        ]
        if ids
        else []
    )
    replies = [
        {"contact_id": r.contact_id, "classification": r.classification}
        for r in session.scalars(select(Reply).where(Reply.campaign_id == campaign_id))
    ]
    return sends, events, replies


def segment_keys(session, contact_ids: set[str]) -> dict[str, dict[str, str]]:
    keys: dict[str, dict[str, str]] = {}
    for cid in contact_ids:
        c = session.get(Contact, cid)
        acc = session.get(Account, c.account_id) if c else None
        lead = session.scalars(select(Lead).where(Lead.contact_id == cid)).first()
        qr = session.get(QualificationResult, lead.qualification_id) if lead and lead.qualification_id else None
        src = session.get(SourceRecord, c.source_ids[0]) if c and c.source_ids else None
        keys[cid] = {
            "industry": (acc.industry if acc else None) or "Unknown",
            "tier": qr.tier if qr else "unscored",
            "source": src.adapter if src else "unknown",
            "signals": "with signals" if acc and acc.signals else "no signals",
        }
    return keys


def compute_campaign_analytics(session, campaign_id: str) -> dict:
    sends, events, replies = campaign_rows(session, campaign_id)
    keys = segment_keys(session, {s["contact_id"] for s in sends})
    out = {
        "campaign_id": campaign_id,
        "simulated": True,
        **metrics.campaign_metrics(sends, events, replies),
        "segments": {},
    }
    for dim in ("industry", "tier", "source", "signals"):
        out["segments"][dim] = metrics.segment_breakdown(sends, events, replies, lambda c, d=dim: keys[c][d])
    return out


# ---------------------------------------------------------------- Performance Analytics


class AnalyticsInput(BaseModel):
    campaign_id: str


class AnalyticsOutput(AgentOutput):
    analytics: dict = Field(default_factory=dict)
    narrative: str = ""


class PerformanceAnalyticsAgent(BaseAgent):
    name = "performance_analytics"
    group = "Measurement & governance"
    description = "Computes campaign metrics with explicit formulas/denominators and segment/source breakdowns."
    tools = ("llm.generate",)
    input_model = AnalyticsInput
    output_model = AnalyticsOutput
    completion_criteria = "Every rate reports numerator, denominator and formula; zero denominators are n/a."

    def run(self, inp: AnalyticsInput) -> AnalyticsOutput:
        a = compute_campaign_analytics(self.session, inp.campaign_id)
        n = self.llm("analytics_narrative", {"counts": a["counts"], "rates": a["rates"]}).text
        sample = a["counts"]["contacts_sent"]
        return AnalyticsOutput(
            analytics=a,
            narrative=n,
            confidence=0.9 if sample >= 10 else 0.6,
            notes=[f"Sample size {sample} contacts - small samples are directional only"],
        )

    def check_completion(self, out: AnalyticsOutput) -> list[str]:
        return [k for k, v in out.analytics.get("rates", {}).items() if "denominator" not in v]


# ---------------------------------------------------------------- ICP Feedback

MIN_SAMPLE = 2


class FeedbackInput(BaseModel):
    campaign_id: str
    icp_version_id: str
    analytics: dict


class Recommendation(BaseModel):
    kind: str
    summary: str
    change: dict
    evidence: str
    applied_to_draft: bool


class FeedbackOutput(AgentOutput):
    recommendations: list[Recommendation] = Field(default_factory=list)
    proposed_icp_version_id: str | None = None


class ICPFeedbackAgent(BaseAgent):
    name = "icp_feedback"
    group = "Measurement & governance"
    description = (
        "Turns outcome analytics into ICP change recommendations and a new draft ICP version. Changes only "
        "take effect after human approval (icp.update)."
    )
    tools = ("llm.generate",)
    input_model = FeedbackInput
    output_model = FeedbackOutput
    confidence_threshold = 0.5
    completion_criteria = (
        "Each recommendation states its evidence and sample size; a proposed version exists if any change."
    )

    def run(self, inp: FeedbackInput) -> FeedbackOutput:
        icp = self.session.get(ICPVersion, inp.icp_version_id)
        assert icp is not None
        a = inp.analytics
        out = FeedbackOutput(confidence=0.55)
        definition, weights = copy.deepcopy(icp.definition), dict(icp.weights)
        seg = {d: {s["segment"]: s for s in a["segments"][d]} for d in a.get("segments", {})}

        def rate(s, k):
            return s["rates"][k]

        # 1. Observed signals vs reply rate
        ws, wo = seg.get("signals", {}).get("with signals"), seg.get("signals", {}).get("no signals")
        if ws and wo:
            r1, r0 = rate(ws, "reply_rate"), rate(wo, "reply_rate")
            if (
                r1["denominator"] >= MIN_SAMPLE
                and r0["denominator"] >= MIN_SAMPLE
                and r1["value"] is not None
                and r0["value"] is not None
                and r1["value"] - r0["value"] >= 0.2
            ):
                move = min(5, weights.get("source_confidence", 0))
                weights["observed_signals"] = weights.get("observed_signals", 0) + move
                weights["source_confidence"] = weights.get("source_confidence", 0) - move
                out.recommendations.append(
                    Recommendation(
                        kind="weight_change",
                        summary=f"Increase observed_signals weight by {move}",
                        change={"observed_signals": f"+{move}", "source_confidence": f"-{move}"},
                        evidence=(
                            f"reply rate with signals {r1['numerator']}/{r1['denominator']} vs without "
                            f"{r0['numerator']}/{r0['denominator']}"
                        ),
                        applied_to_draft=True,
                    )
                )
        # 2. Industries with no human replies among enough delivered contacts -> move to adjacent
        for ind, s in seg.get("industry", {}).items():
            rr = rate(s, "reply_rate")
            if ind in definition.get("industries", []) and rr["denominator"] >= 3 and rr["numerator"] == 0:
                definition["industries"].remove(ind)
                definition.setdefault("adjacent_industries", []).append(ind)
                out.recommendations.append(
                    Recommendation(
                        kind="industry_change",
                        summary=f"Move {ind} from target to adjacent industries",
                        change={"industries": f"-{ind}", "adjacent_industries": f"+{ind}"},
                        evidence=f"0 replies from {rr['denominator']} delivered contacts",
                        applied_to_draft=True,
                    )
                )
        # 3. Sources with high bounce rate -> human review (no automatic change)
        for src, s in seg.get("source", {}).items():
            br = rate(s, "bounce_rate")
            if br["denominator"] >= MIN_SAMPLE and br["value"] is not None and br["value"] >= 0.25:
                out.recommendations.append(
                    Recommendation(
                        kind="source_review",
                        summary=f"Review data quality of source '{src}'",
                        change={},
                        evidence=f"bounce rate {br['numerator']}/{br['denominator']}",
                        applied_to_draft=False,
                    )
                )
        if not out.recommendations:
            out.notes.append("No recommendation met the minimum-sample rules; nothing proposed")
            out.status = "abstained"
            return out
        version = (self.session.scalar(select(func.max(ICPVersion.version))) or 0) + 1
        summary = "; ".join(r.summary for r in out.recommendations)
        narrative = self.llm(
            "recommendation_summary",
            {"recommendation": summary, "evidence": "; ".join(r.evidence for r in out.recommendations)},
        )
        new = ICPVersion(
            version=version,
            status="pending_approval",
            offer_id=icp.offer_id,
            name=f"ICP v{version} (proposed from campaign feedback)",
            definition=definition,
            weights=weights,
            hard_disqualifiers=icp.hard_disqualifiers,
            tier_thresholds=icp.tier_thresholds,
            rationale=narrative.text,
            change_summary=summary,
            parent_version_id=icp.id,
            created_by=f"agent:{self.name}",
        )
        self.session.add(new)
        self.session.flush()
        out.proposed_icp_version_id = new.id
        out.notes.append("Synthetic, small-sample data: recommendations are illustrative and need human judgement")
        return out


# ---------------------------------------------------------------- Compliance & Data Governance

PROHIBITED = [
    r"\bguarantee",
    r"risk[- ]free",
    r"\b100%",
    r"act now",
    r"\bfree money\b",
    r"no obligation whatsoever",
    r"\bclients? like\b",
    r"case stud(y|ies)",
]


class ComplianceInput(BaseModel):
    mode: Literal["draft_policy", "lead_suppression", "event_outcomes", "retention"]
    campaign_id: str | None = None
    lead_ids: list[str] = Field(default_factory=list)


class ComplianceOutput(AgentOutput):
    checked: int = 0
    passed: int = 0
    blocked: dict[str, str] = Field(default_factory=dict)
    actions: list[str] = Field(default_factory=list)


class ComplianceGovernanceAgent(BaseAgent):
    name = "compliance_governance"
    group = "Measurement & governance"
    description = (
        "Deterministic policy checks on drafts (opt-out line, sender identity, prohibited claims, cited "
        "evidence, suppression), lead suppression checks, event-driven suppression and retention sweeps."
    )
    tools = ("suppression.check", "suppression.add")
    input_model = ComplianceInput
    output_model = ComplianceOutput
    completion_criteria = "Every item checked has a recorded pass/fail per rule."

    def run(self, inp: ComplianceInput) -> ComplianceOutput:
        out = ComplianceOutput(confidence=1.0)
        getattr(self, f"_{inp.mode}")(inp, out)
        return out

    def _draft_policy(self, inp: ComplianceInput, out: ComplianceOutput) -> None:
        for d in self.session.scalars(
            select(Draft).where(Draft.campaign_id == inp.campaign_id, Draft.status == "draft")
        ):
            contact = self.session.get(Contact, d.contact_id)
            checks = []

            def chk(rule: str, ok: bool, detail: str = "", _checks: list = checks) -> None:
                _checks.append({"rule": rule, "passed": ok, "detail": detail})

            chk("opt_out_line", "unsubscribe" in d.body.lower(), "body must tell the recipient how to opt out")
            chk("sender_identity", "Northwind Analytics (fictional)" in d.body, "sender organisation named")
            hits = [p for p in PROHIBITED if re.search(p, d.body + " " + d.subject, re.I)]
            chk("no_prohibited_claims", not hits, ", ".join(hits))
            ok_ev = all(self.session.get(Evidence, i) is not None for i in d.evidence_ids)
            chk("evidence_exists", ok_ev, f"{len(d.evidence_ids)} cited")
            chk("personalisation_labelled", d.personalisation_level in ("evidence_grounded", "generic"))
            sup = self.tool("suppression.check", contact=contact) if contact else None
            chk("recipient_not_suppressed", bool(sup) and not sup.blocked, sup.reason if sup and sup.blocked else "")
            chk(
                "consent_basis_recorded",
                bool(contact and contact.consent_basis),
                contact.consent_basis if contact else "",
            )
            chk("subject_length", 0 < len(d.subject) <= 120)
            d.policy_checks = checks
            d.policy_passed = all(c["passed"] for c in checks)
            out.checked += 1
            out.passed += d.policy_passed
            if not d.policy_passed:
                out.blocked[d.id] = ", ".join(c["rule"] for c in checks if not c["passed"])

    def _lead_suppression(self, inp: ComplianceInput, out: ComplianceOutput) -> None:
        for lid in inp.lead_ids:
            lead = self.session.get(Lead, lid)
            contact = self.session.get(Contact, lead.contact_id) if lead else None
            if not lead or not contact:
                continue
            res = self.tool("suppression.check", contact=contact)
            lead.suppression_checked_at = utcnow()
            out.checked += 1
            if res.blocked:
                lead.stage = "suppressed"
                if not contact.suppressed and res.reason != "no_email":
                    contact.suppressed = True
                    contact.suppression_reason = res.reason
                out.blocked[lid] = f"{res.reason} ({res.matched})"
            else:
                out.passed += 1

    def _event_outcomes(self, inp: ComplianceInput, out: ComplianceOutput) -> None:
        from ..verification import status_after_event

        sends = {
            s.id: s
            for s in self.session.scalars(select(SendQueueItem).where(SendQueueItem.campaign_id == inp.campaign_id))
        }
        evs = self.session.scalars(
            select(MessageEvent).where(MessageEvent.send_id.in_(list(sends)), MessageEvent.processed.is_(False))
        ).all()
        for ev in evs:
            contact = self.session.get(Contact, ev.contact_id) if ev.contact_id else None
            if contact is None:
                ev.processed = True
                continue
            if ev.event_type in ("delivered", "bounced"):
                new = status_after_event(contact.email_status, ev.event_type)
                if new != contact.email_status:
                    contact.verification_checks = [
                        *contact.verification_checks,
                        {
                            "check": "delivery_event",
                            "result": ev.event_type,
                            "provider": ev.provider,
                            "mock": True,
                            "simulated": ev.simulated,
                            "at": ev.received_at.isoformat(),
                        },
                    ]
                    out.actions.append(f"{contact.id}: email_status {contact.email_status} -> {new}")
                    contact.email_status = new
            if ev.event_type == "bounced" and contact.email:
                self.tool("suppression.add", value=contact.email, reason="bounce", note=f"event {ev.id}")
                out.actions.append(f"{contact.id}: suppressed (bounce)")
            if ev.event_type in ("unsubscribed", "complaint") and contact.email:
                self.tool(
                    "suppression.add",
                    value=contact.email,
                    reason="opt_out" if ev.event_type == "unsubscribed" else "complaint",
                    note=f"event {ev.id}",
                )
                out.actions.append(f"{contact.id}: suppressed ({ev.event_type})")
            ev.processed = True
            out.checked += 1
        for rep in self.session.scalars(
            select(Reply).where(Reply.campaign_id == inp.campaign_id, Reply.classification == "unsubscribe")
        ):
            contact = self.session.get(Contact, rep.contact_id)
            if contact and contact.email and not contact.suppressed:
                self.tool("suppression.add", value=contact.email, reason="opt_out", note=f"reply {rep.id}")
                out.actions.append(f"{contact.id}: suppressed (unsubscribe reply)")

    def _retention(self, inp: ComplianceInput, out: ComplianceOutput) -> None:
        """Delete contacts whose every source is past its adapter's retention period (and that never engaged)."""
        now = utcnow()
        retention = {a.id: a.retention_days for a in self.session.scalars(select(SourceAdapter))}
        for c in self.session.scalars(select(Contact)).all():
            srcs = [self.session.get(SourceRecord, s) for s in c.source_ids]
            srcs = [s for s in srcs if s]
            if not srcs:
                continue
            expired = all(s.retrieved_at < now - timedelta(days=retention.get(s.adapter, 365)) for s in srcs)
            out.checked += 1
            if expired:
                lead = self.session.scalars(select(Lead).where(Lead.contact_id == c.id)).first()
                if lead and lead.stage in ("interested", "replied"):
                    continue
                out.actions.append(f"deleted contact {c.id} (retention)")
                self.session.delete(c)
        out.passed = out.checked - len(out.actions)


# ---------------------------------------------------------------- Workflow Supervisor


class SupervisorInput(BaseModel):
    stale_after_seconds: int = 300
    approval_sla_hours: int = 48


class SupervisorOutput(AgentOutput):
    recovered_steps: list[str] = Field(default_factory=list)
    open_errors: int = 0
    overdue_approvals: list[str] = Field(default_factory=list)
    runs_by_status: dict[str, int] = Field(default_factory=dict)
    emergency_stop: bool = False


class WorkflowSupervisorAgent(BaseAgent):
    name = "workflow_supervisor"
    group = "Measurement & governance"
    description = (
        "Monitors runs: recovers steps left 'running' by a crashed worker (expired lease), reports the "
        "error queue and overdue approvals. It can never approve, retry past limits or lift the stop."
    )
    tools = ()
    input_model = SupervisorInput
    output_model = SupervisorOutput
    completion_criteria = "Report produced; every stale running step is reset to pending."

    def run(self, inp: SupervisorInput) -> SupervisorOutput:
        from ..system import get_state

        now = utcnow()
        out = SupervisorOutput(confidence=1.0, emergency_stop=get_state(self.session).emergency_stop)
        for st in self.session.scalars(select(WorkflowStep).where(WorkflowStep.status == "running")):
            run = self.session.get(WorkflowRun, st.run_id)
            lease_ok = run and run.lease_until and run.lease_until > now
            if not lease_ok and st.started_at and st.started_at < now - timedelta(seconds=inp.stale_after_seconds):
                st.status = "pending"
                st.error = "recovered by supervisor after worker crash"
                out.recovered_steps.append(st.id)
        out.open_errors = (
            self.session.scalar(select(func.count()).select_from(ErrorQueueItem).where(ErrorQueueItem.status == "open"))
            or 0
        )
        out.overdue_approvals = [
            a.id
            for a in self.session.scalars(
                select(Approval).where(
                    Approval.status == "pending", Approval.created_at < now - timedelta(hours=inp.approval_sla_hours)
                )
            )
        ]
        for status, n in self.session.execute(select(WorkflowRun.status, func.count()).group_by(WorkflowRun.status)):
            out.runs_by_status[status] = n
        return out
