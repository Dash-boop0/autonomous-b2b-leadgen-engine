"""Workflow definitions, runs (request-driven advance), error queue and approvals."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import approvals, audit
from ..db import get_session
from ..models import Approval, Campaign, Draft, ErrorQueueItem, ICPVersion, WorkflowRun, WorkflowStep, utcnow
from ..schemas import Approval as ApprovalOut
from ..security import SYSTEM, Actor, require
from ..workflows import engine as wf
from .util import to_dict

router = APIRouter(prefix="/api")


@router.get("/workflows")
def workflows(_: Actor = Depends(require("read"))) -> list[dict]:
    return [
        {
            "name": w.name,
            "title": w.title,
            "description": w.description,
            "agents": w.agents,
            "input_schema": w.input_model.model_json_schema(),
            "steps": [
                {
                    "name": s.name,
                    "agent": s.agent,
                    "title": s.title,
                    "max_attempts": s.max_attempts,
                    "kind": "approval_gate"
                    if s.name.startswith("approve")
                    else ("agent" if s.agent else "deterministic"),
                }
                for s in w.steps
            ],
        }
        for w in wf.REGISTRY.values()
    ]


class StartIn(BaseModel):
    input: dict = Field(default_factory=dict)
    idempotency_key: str | None = None


def run_out(session: Session, run: WorkflowRun) -> dict:
    steps = session.scalars(select(WorkflowStep).where(WorkflowStep.run_id == run.id).order_by(WorkflowStep.idx)).all()
    d = to_dict(run, exclude={"context"})
    d["title"] = wf.REGISTRY[run.workflow].title
    d["steps"] = [to_dict(s) for s in steps]
    return d


@router.post("/workflows/{name}/runs")
def start(
    name: str, body: StartIn, session: Session = Depends(get_session), actor: Actor = Depends(require("workflows.run"))
) -> dict:
    if name not in wf.REGISTRY:
        raise HTTPException(404, "unknown workflow")
    try:
        run = wf.start_run(session, name, body.input, actor, body.idempotency_key)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    res = wf.advance(session, run.id)
    session.refresh(run)
    return {"advance": res, "run": run_out(session, run)}


@router.get("/runs")
def runs(
    workflow: str | None = None,
    status: str | None = None,
    limit: int = 50,
    session: Session = Depends(get_session),
    _: Actor = Depends(require("read")),
) -> list[dict]:
    q = select(WorkflowRun).order_by(WorkflowRun.created_at.desc()).limit(min(limit, 200))
    if workflow:
        q = q.where(WorkflowRun.workflow == workflow)
    if status:
        q = q.where(WorkflowRun.status == status)
    return [run_out(session, r) for r in session.scalars(q)]


@router.get("/runs/{run_id}")
def run_detail(run_id: str, session: Session = Depends(get_session), _: Actor = Depends(require("read"))) -> dict:
    r = session.get(WorkflowRun, run_id)
    if r is None:
        raise HTTPException(404, "run not found")
    return run_out(session, r)


@router.post("/runs/{run_id}/advance")
def advance(run_id: str, session: Session = Depends(get_session), _: Actor = Depends(require("workflows.run"))) -> dict:
    res = wf.advance(session, run_id)
    return {"advance": res, "run": run_out(session, session.get(WorkflowRun, run_id))}


@router.post("/runs/{run_id}/pause")
def pause(
    run_id: str, session: Session = Depends(get_session), actor: Actor = Depends(require("workflows.run"))
) -> dict:
    return run_out(session, wf.pause(session, run_id, actor))


@router.post("/runs/{run_id}/resume")
def resume(
    run_id: str, session: Session = Depends(get_session), actor: Actor = Depends(require("workflows.run"))
) -> dict:
    res = wf.resume(session, run_id, actor)
    return {"advance": res, "run": run_out(session, session.get(WorkflowRun, run_id))}


class CancelIn(BaseModel):
    reason: str = "cancelled by user"


@router.post("/runs/{run_id}/cancel")
def cancel(
    run_id: str,
    body: CancelIn,
    session: Session = Depends(get_session),
    actor: Actor = Depends(require("workflows.run")),
) -> dict:
    return run_out(session, wf.cancel(session, run_id, actor, body.reason))


@router.get("/errors")
def errors(session: Session = Depends(get_session), _: Actor = Depends(require("read"))) -> list[dict]:
    return [to_dict(e) for e in session.scalars(select(ErrorQueueItem).order_by(ErrorQueueItem.created_at.desc()))]


@router.post("/errors/{error_id}/retry")
def retry_error(
    error_id: str, session: Session = Depends(get_session), actor: Actor = Depends(require("workflows.run"))
) -> dict:
    return wf.retry_error(session, error_id, actor)


@router.post("/errors/{error_id}/dismiss")
def dismiss_error(
    error_id: str, session: Session = Depends(get_session), actor: Actor = Depends(require("workflows.run"))
) -> dict:
    e = session.get(ErrorQueueItem, error_id)
    if e is None or e.status != "open":
        raise HTTPException(404, "open error not found")
    e.status, e.resolved_by = "dismissed", actor.label
    audit.record(session, actor, "workflow.error_dismissed", "error_queue", e.id, run_id=e.run_id)
    session.commit()
    return to_dict(e)


# ---------------------------------------------------------------- approvals


def approval_out(session: Session, a: Approval) -> dict:
    d = ApprovalOut.from_row(a).model_dump(mode="json")
    d.update(
        {
            "summary": a.summary,
            "payload": a.payload,
            "run_id": a.run_id,
            "initiated_by": a.initiated_by,
            "created_at": a.created_at.isoformat(),
        }
    )
    if a.subject_type == "campaign":
        c = session.get(Campaign, a.subject_id)
        d["subject_name"] = c.name if c else None
    elif a.subject_type == "icp_version":
        i = session.get(ICPVersion, a.subject_id)
        d["subject_name"] = i.name if i else None
    return d


@router.get("/approvals")
def list_approvals(
    status: str | None = None, session: Session = Depends(get_session), _: Actor = Depends(require("read"))
) -> list[dict]:
    q = select(Approval).order_by(Approval.created_at.desc())
    if status:
        q = q.where(Approval.status == status)
    return [approval_out(session, a) for a in session.scalars(q)]


class DecideIn(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    reason: str | None = None


def _side_effects_without_run(session: Session, a: Approval) -> None:
    """Approvals requested outside a workflow run (manual ICP edits) apply their effect here, deterministically."""
    if a.subject_type == "icp_version":
        i = session.get(ICPVersion, a.subject_id)
        if i is None:
            return
        if a.status == "approved":
            for other in session.scalars(select(ICPVersion).where(ICPVersion.status == "approved")):
                other.status = "superseded"
            i.status, i.approved_at, i.approval_id = "approved", utcnow(), a.id
            audit.record(session, SYSTEM, "icp.activated", "icp_version", i.id, after={"version": i.version})
        else:
            i.status = "rejected"
        session.commit()


@router.post("/approvals/{approval_id}/decide")
def decide(
    approval_id: str,
    body: DecideIn,
    session: Session = Depends(get_session),
    actor: Actor = Depends(require("approvals.decide")),
) -> dict:
    try:
        a = approvals.decide(session, approval_id, actor, body.decision, body.reason)
    except approvals.ApprovalError as e:
        session.commit()  # keep the approval.denied audit event
        raise HTTPException(403, str(e)) from e
    session.commit()
    resumed = wf.resume_after_decision(session, a) if a.run_id else _side_effects_without_run(session, a)
    session.refresh(a)
    return {"approval": approval_out(session, a), "resumed": resumed}


class DraftDecision(BaseModel):
    status: str = Field(pattern="^(draft|rejected)$")
    reason: str | None = None


@router.post("/drafts/{draft_id}/review")
def review_draft(
    draft_id: str,
    body: DraftDecision,
    session: Session = Depends(get_session),
    actor: Actor = Depends(require("approvals.decide")),
) -> dict:
    """A reviewer can exclude individual drafts before approving the campaign (rejected drafts are never queued)."""
    d = session.get(Draft, draft_id)
    if d is None:
        raise HTTPException(404, "draft not found")
    if d.status not in ("draft", "rejected"):
        raise HTTPException(409, f"draft is {d.status}")
    before = d.status
    d.status = body.status
    audit.record(
        session,
        actor,
        f"draft.{'rejected' if body.status == 'rejected' else 'restored'}",
        "draft",
        d.id,
        before={"status": before},
        after={"status": d.status, "reason": body.reason},
    )
    session.commit()
    return to_dict(d)
