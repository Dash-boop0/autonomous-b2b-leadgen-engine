"""Deterministic approval engine. LLMs and agents can request approvals but can never decide them.

Rules (enforced here, not in any prompt):
  * only human actors with the ``approvals.decide`` permission can decide;
  * ``decided_by != requested_by`` (no self-approval);
  * four-eyes policies also forbid the human who initiated the workflow run from approving it;
  * only pending approvals can be decided; rejections need a reason.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit
from .models import Approval, utcnow
from .security import Actor


@dataclass(frozen=True)
class Policy:
    id: str
    description: str
    four_eyes: bool = True


POLICIES: dict[str, Policy] = {
    "icp.activate": Policy("pol_icp_activate_v1", "A new ICP/scoring model must be approved by a reviewer before use."),
    "icp.update": Policy("pol_icp_update_v1", "ICP changes recommended from performance data need human review."),
    "campaign.activate": Policy(
        "pol_campaign_activate_v1", "Campaign drafts, sequence and audience must be approved before anything is queued."
    ),
}


class ApprovalError(PermissionError):
    pass


def request(
    session: Session,
    *,
    subject_type: str,
    subject_id: str,
    action: str,
    requested_by: Actor,
    initiated_by: str | None,
    run_id: str | None = None,
    step_id: str | None = None,
    summary: str = "",
    payload: dict | None = None,
) -> Approval:
    policy = POLICIES[action]
    existing = session.scalars(
        select(Approval).where(
            Approval.subject_type == subject_type,
            Approval.subject_id == subject_id,
            Approval.action == action,
            Approval.run_id == run_id,
        )
    ).first()
    if existing:
        return existing
    apr = Approval(
        subject_type=subject_type,
        subject_id=subject_id,
        action=action,
        requested_by_type=requested_by.actor_type,
        requested_by_id=requested_by.actor_id,
        status="pending",
        policy_id=policy.id,
        initiated_by=initiated_by,
        run_id=run_id,
        step_id=step_id,
        summary=summary,
        payload=payload or {},
    )
    session.add(apr)
    session.flush()
    audit.record(
        session,
        requested_by,
        "approval.requested",
        "approval",
        apr.id,
        after={"action": action, "subject": f"{subject_type}:{subject_id}"},
        run_id=run_id,
        step_id=step_id,
    )
    return apr


def check_can_decide(apr: Approval, actor: Actor) -> None:
    if actor.actor_type != "human":
        raise ApprovalError("Agents and system processes can never decide approvals")
    if not actor.can("approvals.decide"):
        raise ApprovalError(f"Role '{actor.role}' cannot decide approvals")
    if apr.status != "pending":
        raise ApprovalError(f"Approval is already {apr.status}")
    if actor.actor_id == apr.requested_by_id:
        raise ApprovalError("Self-approval is not allowed (decided_by must differ from requested_by)")
    policy = POLICIES.get(apr.action)
    if policy and policy.four_eyes and apr.initiated_by and actor.actor_id == apr.initiated_by:
        raise ApprovalError("Four-eyes policy: the person who started this workflow cannot approve it")


def decide(session: Session, approval_id: str, actor: Actor, decision: str, reason: str | None) -> Approval:
    apr = session.get(Approval, approval_id)
    if apr is None:
        raise LookupError("Approval not found")
    if decision not in ("approved", "rejected"):
        raise ValueError("decision must be 'approved' or 'rejected'")
    try:
        check_can_decide(apr, actor)
        if decision == "rejected" and not (reason or "").strip():
            raise ApprovalError("A reason is required to reject")
    except ApprovalError as e:
        audit.record(
            session,
            actor,
            "approval.denied",
            "approval",
            apr.id,
            after={"error": str(e)},
            severity="warn",
            run_id=apr.run_id,
        )
        raise
    before = {"status": apr.status}
    apr.status = decision
    apr.decided_by = actor.actor_id
    apr.decided_at = utcnow()
    apr.reason = reason
    audit.record(
        session,
        actor,
        f"approval.{decision}",
        "approval",
        apr.id,
        before=before,
        after={"status": decision, "reason": reason, "action": apr.action, "subject_id": apr.subject_id},
        run_id=apr.run_id,
        step_id=apr.step_id,
    )
    return apr


def is_approved(session: Session, subject_type: str, subject_id: str, action: str) -> bool:
    return (
        session.scalars(
            select(Approval).where(
                Approval.subject_type == subject_type,
                Approval.subject_id == subject_id,
                Approval.action == action,
                Approval.status == "approved",
            )
        ).first()
        is not None
    )


def assert_approved(session: Session, subject_type: str, subject_id: str, action: str) -> None:
    if not is_approved(session, subject_type, subject_id, action):
        raise ApprovalError(f"{action} for {subject_type}:{subject_id} has not been approved by a human")
