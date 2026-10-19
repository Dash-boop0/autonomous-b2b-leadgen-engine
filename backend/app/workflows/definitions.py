"""Workflow definitions as code. Each step is either an agent call or deterministic code; approval gates are
explicit steps that only a human decision can pass."""

from __future__ import annotations

from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import audit
from ..adapters import events as events_adapter
from ..agents import AgentContext
from ..agents.discovery import DataEnrichmentAgent, NormalisedAccount, upsert_accounts
from ..models import (
    Account,
    Approval,
    Campaign,
    Contact,
    Draft,
    ICPVersion,
    Lead,
    QualificationResult,
    SendQueueItem,
    utcnow,
)
from ..security import SYSTEM
from .engine import PermanentStepError, StepContext, StepDef, WorkflowDef, register

QUALIFYING_EMAIL = ("domain_has_mx", "provider_verified", "deliverable_confirmed")


def _icp(ctx: StepContext, icp_id: str) -> ICPVersion:
    icp = ctx.session.get(ICPVersion, icp_id)
    if icp is None:
        raise PermanentStepError(f"ICP {icp_id} not found")
    return icp


# ================================================================ 1. Market-to-ICP


class MarketToICPInput(BaseModel):
    offer_id: str


def s_market_research(ctx: StepContext) -> dict:
    return ctx.agent("market_research", {"offer_id": ctx.input["offer_id"]})


def s_personas(ctx: StepContext) -> dict:
    segs = ctx.out("market_research")["segments"]
    return ctx.agent("buyer_persona", {"offer_id": ctx.input["offer_id"], "industries": [s["industry"] for s in segs]})


def s_competitors(ctx: StepContext) -> dict:
    return ctx.agent("competitor_gap", {"offer_id": ctx.input["offer_id"]})


def s_build_icp(ctx: StepContext) -> dict:
    return ctx.agent(
        "icp_builder",
        {
            "offer_id": ctx.input["offer_id"],
            "segments": ctx.out("market_research")["segments"],
            "personas": ctx.out("buyer_personas")["personas"],
            "competitor_names": ctx.out("competitor_analysis")["competitor_names"],
        },
    )


def _icp_gate(ctx: StepContext, icp_id: str, action: str, requested_by: str) -> dict:
    icp = _icp(ctx, icp_id)

    def rejected(apr: Approval) -> None:
        icp.status = "rejected"
        audit.record(
            ctx.session, SYSTEM, "icp.rejected", "icp_version", icp.id, after={"reason": apr.reason}, run_id=ctx.run.id
        )

    return ctx.gate(
        subject_type="icp_version",
        subject_id=icp.id,
        action=action,
        requested_by=requested_by,
        summary=f"{icp.name}: {icp.change_summary or icp.rationale[:160]}",
        payload={"version": icp.version, "weights": icp.weights},
        on_rejected=rejected,
    )


def s_approve_icp(ctx: StepContext) -> dict:
    return _icp_gate(ctx, ctx.out("build_icp")["icp_version_id"], "icp.activate", "icp_builder")


def _activate(ctx: StepContext, icp_id: str, approval_step: str) -> dict:
    icp = _icp(ctx, icp_id)
    if icp.status == "approved":
        return {"icp_version_id": icp.id, "status": "approved", "note": "already active"}
    for other in ctx.session.scalars(select(ICPVersion).where(ICPVersion.status == "approved")):
        other.status = "superseded"
    icp.status = "approved"
    icp.approved_at = utcnow()
    icp.approval_id = ctx.out(approval_step).get("approval_id")
    audit.record(
        ctx.session,
        SYSTEM,
        "icp.activated",
        "icp_version",
        icp.id,
        after={"version": icp.version},
        run_id=ctx.run.id,
        step_id=ctx.step.id,
    )
    return {"icp_version_id": icp.id, "status": "approved", "version": icp.version}


def s_activate_icp(ctx: StepContext) -> dict:
    return _activate(ctx, ctx.out("build_icp")["icp_version_id"], "approve_icp")


register(
    WorkflowDef(
        "market_to_icp",
        "Market-to-ICP",
        "Offer -> research -> segments -> personas -> competitor analysis -> ICP/scoring model -> human approval",
        [
            StepDef("market_research", s_market_research, "market_research"),
            StepDef("buyer_personas", s_personas, "buyer_persona"),
            StepDef("competitor_analysis", s_competitors, "competitor_gap"),
            StepDef("build_icp", s_build_icp, "icp_builder"),
            StepDef("approve_icp", s_approve_icp, None, title="Human approval: icp.activate"),
            StepDef("activate_icp", s_activate_icp),
        ],
        MarketToICPInput,
    )
)


# ================================================================ 2. ICP-to-account list


class ICPToAccountsInput(BaseModel):
    icp_version_id: str
    adapters: list[str] = Field(default_factory=lambda: ["csv_upload", "mock_registry"])
    csv_upload_id: str | None = None


def s_check_icp(ctx: StepContext) -> dict:
    icp = _icp(ctx, ctx.input["icp_version_id"])
    if icp.status != "approved":
        raise PermanentStepError(f"ICP v{icp.version} is {icp.status}; a human must approve it first")
    return {"icp_version": icp.version}


def s_discover_accounts(ctx: StepContext) -> dict:
    return ctx.agent("account_discovery", ctx.input)


def s_normalise(ctx: StepContext) -> dict:
    return ctx.agent("data_enrichment", {"mode": "accounts", "candidates": ctx.out("discover")["candidates"]})


def s_dedup(ctx: StepContext) -> dict:
    """Deterministic merge by dedup key; evidence written under the data_enrichment agent's name."""
    ag = DataEnrichmentAgent(AgentContext(ctx.session, ctx.run.id, ctx.step.id))
    accounts = [NormalisedAccount.model_validate(a) for a in ctx.out("normalise")["accounts"]]
    res = upsert_accounts(ag, accounts)
    audit.record(
        ctx.session,
        SYSTEM,
        "accounts.deduplicated",
        "workflow_run",
        ctx.run.id,
        after={"input_records": len(accounts), "created": res["created"], "merged": res["merged"]},
        evidence_ids=ag._evidence[:200],
        run_id=ctx.run.id,
        step_id=ctx.step.id,
    )
    return {**res, "input_records": len(accounts)}


def s_web_research(ctx: StepContext) -> dict:
    return ctx.agent("public_web_research", {"account_ids": ctx.out("dedup")["account_ids"]})


def s_signals(ctx: StepContext) -> dict:
    researched = [f["account_id"] for f in ctx.out("public_web_research")["findings"] if f["status"] == "ok"]
    return ctx.agent(
        "buying_signal", {"account_ids": ctx.out("dedup")["account_ids"], "researched_account_ids": researched}
    )


def s_score_accounts(ctx: StepContext) -> dict:
    return ctx.agent(
        "lead_scoring",
        {"entity": "account", "ids": ctx.out("dedup")["account_ids"], "icp_version_id": ctx.input["icp_version_id"]},
    )


register(
    WorkflowDef(
        "icp_to_accounts",
        "ICP-to-account list",
        "Approved ICP -> permitted discovery -> normalisation -> dedup -> evidence -> account scoring",
        [
            StepDef("check_icp", s_check_icp, max_attempts=1),
            StepDef("discover", s_discover_accounts, "account_discovery"),
            StepDef("normalise", s_normalise, "data_enrichment"),
            StepDef("dedup", s_dedup),
            StepDef("public_web_research", s_web_research, "public_web_research"),
            StepDef("buying_signals", s_signals, "buying_signal"),
            StepDef("score_accounts", s_score_accounts, "lead_scoring"),
        ],
        ICPToAccountsInput,
    )
)


# ================================================================ 3. Account-to-contact list


class AccountToContactsInput(BaseModel):
    icp_version_id: str
    account_ids: list[str] = Field(default_factory=list)
    adapters: list[str] = Field(default_factory=lambda: ["csv_upload", "licensed_contacts"])
    csv_upload_id: str | None = None


def s_select_accounts(ctx: StepContext) -> dict:
    icp = _icp(ctx, ctx.input["icp_version_id"])
    if icp.status != "approved":
        raise PermanentStepError("ICP must be approved")
    ids = ctx.input.get("account_ids") or []
    chosen, skipped = [], {}
    pool = [ctx.session.get(Account, i) for i in ids] if ids else ctx.session.scalars(select(Account)).all()
    for acc in pool:
        if acc is None:
            continue
        qr = ctx.session.get(QualificationResult, acc.qualification_id) if acc.qualification_id else None
        if qr is None or qr.icp_version_id != icp.id:
            skipped[acc.id] = "not scored with this ICP"
        elif qr.tier == "disqualified":
            skipped[acc.id] = "disqualified: " + ", ".join(d["rule"] for d in qr.hard_disqualifiers)
        else:
            chosen.append(acc.id)
    target_roles = icp.definition.get("buyer_roles", [])
    return {"account_ids": sorted(chosen), "skipped": skipped, "target_roles": target_roles}


def s_discover_contacts(ctx: StepContext) -> dict:
    return ctx.agent(
        "contact_discovery",
        {
            "icp_version_id": ctx.input["icp_version_id"],
            "account_ids": ctx.out("select_accounts")["account_ids"],
            "adapters": ctx.input["adapters"],
            "csv_upload_id": ctx.input.get("csv_upload_id"),
        },
    )


def s_enrich_contacts(ctx: StepContext) -> dict:
    return ctx.agent(
        "data_enrichment", {"mode": "contacts", "contact_ids": ctx.out("discover_contacts")["contact_ids"]}
    )


def s_verify(ctx: StepContext) -> dict:
    return ctx.agent("contact_verification", {"contact_ids": ctx.out("discover_contacts")["contact_ids"]})


def s_create_leads(ctx: StepContext) -> dict:
    created, ids = 0, []
    for cid in ctx.out("discover_contacts")["contact_ids"]:
        c = ctx.session.get(Contact, cid)
        if c is None:
            continue
        lead = ctx.session.scalars(select(Lead).where(Lead.contact_id == cid)).first()
        if lead is None:
            lead = Lead(account_id=c.account_id, contact_id=c.id, stage="new", owner=ctx.run.created_by)
            ctx.session.add(lead)
            ctx.session.flush()
            created += 1
        ids.append(lead.id)
    return {"lead_ids": ids, "created": created}


def s_qualify(ctx: StepContext) -> dict:
    out = ctx.agent(
        "lead_scoring",
        {"entity": "lead", "ids": ctx.out("create_leads")["lead_ids"], "icp_version_id": ctx.input["icp_version_id"]},
    )
    decisions = {}
    for r in out["results"]:
        lead = ctx.session.get(Lead, r["entity_id"])
        contact = ctx.session.get(Contact, lead.contact_id) if lead else None
        if not lead or not contact or lead.stage not in ("new", "qualified", "disqualified"):
            continue
        reasons = []
        if r["tier"] not in ("A", "B"):
            reasons.append(f"tier {r['tier']}")
        if contact.buyer_role == "unknown":
            reasons.append("no buyer-role match")
        if contact.email_status not in QUALIFYING_EMAIL:
            reasons.append(f"email {contact.email_status}")
        elif any(c.get("check") == "provider" and c.get("result") == "invalid" for c in contact.verification_checks):
            reasons.append("verification provider reported the mailbox invalid")
        lead.stage = "disqualified" if reasons else "qualified"
        decisions[lead.id] = {"stage": lead.stage, "reasons": reasons}
    out["decisions"] = decisions
    return out


def s_lead_suppression(ctx: StepContext) -> dict:
    return ctx.agent(
        "compliance_governance", {"mode": "lead_suppression", "lead_ids": ctx.out("create_leads")["lead_ids"]}
    )


def s_rescore_accounts(ctx: StepContext) -> dict:
    return ctx.agent(
        "lead_scoring",
        {
            "entity": "account",
            "ids": ctx.out("select_accounts")["account_ids"],
            "icp_version_id": ctx.input["icp_version_id"],
        },
    )


register(
    WorkflowDef(
        "account_to_contacts",
        "Account-to-contact list",
        "Accounts -> role matching -> authorised contact discovery -> enrichment -> verification -> qualification -> "
        "suppression checks",
        [
            StepDef("select_accounts", s_select_accounts, max_attempts=1),
            StepDef("discover_contacts", s_discover_contacts, "contact_discovery"),
            StepDef("enrich_contacts", s_enrich_contacts, "data_enrichment"),
            StepDef("verify_contacts", s_verify, "contact_verification"),
            StepDef("create_leads", s_create_leads),
            StepDef("qualify_leads", s_qualify, "lead_scoring"),
            StepDef("suppression_check", s_lead_suppression, "compliance_governance"),
            StepDef("rescore_accounts", s_rescore_accounts, "lead_scoring"),
        ],
        AccountToContactsInput,
    )
)


# ================================================================ 4. Lead-to-campaign


class CampaignInput(BaseModel):
    campaign_id: str


def _campaign(ctx: StepContext) -> Campaign:
    c = ctx.session.get(Campaign, ctx.input["campaign_id"])
    if c is None:
        raise PermanentStepError("campaign not found")
    return c


def s_plan(ctx: StepContext) -> dict:
    return ctx.agent("campaign_planning", {"campaign_id": ctx.input["campaign_id"]})


def s_personalise(ctx: StepContext) -> dict:
    return ctx.agent(
        "personalisation", {"campaign_id": ctx.input["campaign_id"], "lead_ids": ctx.out("plan")["selected_lead_ids"]}
    )


def s_policy(ctx: StepContext) -> dict:
    return ctx.agent("compliance_governance", {"mode": "draft_policy", "campaign_id": ctx.input["campaign_id"]})


def s_approve_campaign(ctx: StepContext) -> dict:
    camp = _campaign(ctx)
    drafts = ctx.session.scalars(select(Draft).where(Draft.campaign_id == camp.id)).all()
    passed = sum(d.policy_passed for d in drafts)
    if camp.status == "draft":
        camp.status = "pending_approval"

    def rejected(apr: Approval) -> None:
        camp.status = "rejected"

    res = ctx.gate(
        subject_type="campaign",
        subject_id=camp.id,
        action="campaign.activate",
        requested_by="outreach_operations",
        summary=f"{camp.name}: {len(drafts)} drafts ({passed} pass policy) for "
        f"{len({d.contact_id for d in drafts})} contacts; dry-run only",
        payload={"drafts": len(drafts), "policy_passed": passed},
        on_rejected=rejected,
    )
    camp.approval_id = res["approval_id"]
    camp.status = "approved"
    return res


def s_queue(ctx: StepContext) -> dict:
    return ctx.agent("outreach_operations", {"campaign_id": ctx.input["campaign_id"], "mode": "queue"})


register(
    WorkflowDef(
        "lead_to_campaign",
        "Lead-to-campaign",
        "Qualified leads -> segmentation -> strategy -> personalised drafts -> policy checks -> approval -> sending queue",
        [
            StepDef("plan", s_plan, "campaign_planning"),
            StepDef("personalise", s_personalise, "personalisation"),
            StepDef("policy_check", s_policy, "compliance_governance"),
            StepDef("approve_campaign", s_approve_campaign, None, title="Human approval: campaign.activate"),
            StepDef("queue", s_queue, "outreach_operations"),
        ],
        CampaignInput,
    )
)


# ================================================================ 5. Campaign-to-conversation


def s_dispatch(ctx: StepContext) -> dict:
    return ctx.agent("outreach_operations", {"campaign_id": ctx.input["campaign_id"], "mode": "dispatch"})


def s_collect_events(ctx: StepContext) -> dict:
    """Mock mailbox: simulated delivery/reply/bounce events for dry-run sends (idempotent per provider event id)."""
    sends = ctx.session.scalars(
        select(SendQueueItem).where(
            SendQueueItem.campaign_id == ctx.input["campaign_id"], SendQueueItem.status == "sent_simulated"
        )
    ).all()
    res = events_adapter.simulate_for_sends(ctx.session, list(sends))
    audit.record(
        ctx.session,
        SYSTEM,
        "events.collected_simulated",
        "campaign",
        ctx.input["campaign_id"],
        after=res,
        run_id=ctx.run.id,
        step_id=ctx.step.id,
    )
    return res


def s_classify(ctx: StepContext) -> dict:
    return ctx.agent("reply_classification", {"campaign_id": ctx.input["campaign_id"]})


def s_event_suppression(ctx: StepContext) -> dict:
    return ctx.agent("compliance_governance", {"mode": "event_outcomes", "campaign_id": ctx.input["campaign_id"]})


def s_cancel_followups(ctx: StepContext) -> dict:
    return ctx.agent("follow_up", {"campaign_id": ctx.input["campaign_id"], "mode": "cancel"})


def s_responses(ctx: StepContext) -> dict:
    return ctx.agent("follow_up", {"campaign_id": ctx.input["campaign_id"], "mode": "respond"})


def s_crm(ctx: StepContext) -> dict:
    return ctx.agent("outreach_operations", {"campaign_id": ctx.input["campaign_id"], "mode": "crm_sync"})


register(
    WorkflowDef(
        "campaign_to_conversation",
        "Campaign-to-conversation",
        "Approved send (dry-run) -> delivery/reply events -> classification -> suppression -> follow-up cancellation -> "
        "response drafts -> CRM updates",
        [
            StepDef("dispatch_due", s_dispatch, "outreach_operations"),
            StepDef("collect_events", s_collect_events),
            StepDef("classify_replies", s_classify, "reply_classification"),
            StepDef("apply_suppression", s_event_suppression, "compliance_governance"),
            StepDef("cancel_followups", s_cancel_followups, "follow_up"),
            StepDef("draft_responses", s_responses, "follow_up"),
            StepDef("crm_update", s_crm, "outreach_operations"),
        ],
        CampaignInput,
    )
)


# ================================================================ 6. Performance-to-improvement


class PerformanceInput(BaseModel):
    campaign_id: str
    icp_version_id: str


def s_analytics(ctx: StepContext) -> dict:
    return ctx.agent("performance_analytics", {"campaign_id": ctx.input["campaign_id"]})


def s_recommend(ctx: StepContext) -> dict:
    return ctx.agent(
        "icp_feedback",
        {
            "campaign_id": ctx.input["campaign_id"],
            "icp_version_id": ctx.input["icp_version_id"],
            "analytics": ctx.out("analytics")["analytics"],
        },
    )


def s_approve_update(ctx: StepContext) -> dict:
    pid = ctx.out("recommend").get("proposed_icp_version_id")
    if not pid:
        return {"skipped": True, "reason": "no proposed change"}
    return _icp_gate(ctx, pid, "icp.update", "icp_feedback")


def s_activate_update(ctx: StepContext) -> dict:
    pid = ctx.out("recommend").get("proposed_icp_version_id")
    if not pid:
        return {"skipped": True}
    return _activate(ctx, pid, "approve_icp_update")


register(
    WorkflowDef(
        "performance_to_improvement",
        "Performance-to-improvement",
        "Outcomes -> segment/source analysis -> ICP recommendations -> human review -> versioned update",
        [
            StepDef("analytics", s_analytics, "performance_analytics"),
            StepDef("recommend", s_recommend, "icp_feedback"),
            StepDef("approve_icp_update", s_approve_update, None, title="Human approval: icp.update"),
            StepDef("activate_new_version", s_activate_update),
        ],
        PerformanceInput,
    )
)


# ================================================================ 7. Governance sweep


class SweepInput(BaseModel):
    stale_after_seconds: int = 300


def s_supervise(ctx: StepContext) -> dict:
    return ctx.agent("workflow_supervisor", {"stale_after_seconds": ctx.input["stale_after_seconds"]})


def s_retention(ctx: StepContext) -> dict:
    return ctx.agent("compliance_governance", {"mode": "retention"})


register(
    WorkflowDef(
        "governance_sweep",
        "Governance sweep",
        "Workflow supervision (stale-step recovery, error queue, overdue approvals) + data-retention sweep",
        [
            StepDef("supervisor_check", s_supervise, "workflow_supervisor"),
            StepDef("retention_sweep", s_retention, "compliance_governance"),
        ],
        SweepInput,
    )
)
