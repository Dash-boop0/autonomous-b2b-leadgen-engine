"""Durable, DB-backed workflow engine (no external queue). Request-driven: callers advance a run with
advance(run_id, budget) which executes steps until the run blocks (approval, backoff, pause, stop) or the wall-clock
budget is spent. Safe on serverless: a lease row-update prevents two requests executing the same run concurrently,
every step commits its own transaction, and a crashed step is retried after its lease expires."""

from __future__ import annotations

import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from .. import approvals, audit
from ..adapters.sources import AdapterError
from ..agents import AGENTS
from ..agents.base import AgentContext, AgentIncomplete
from ..config import get_settings
from ..models import Approval, ErrorQueueItem, WorkflowRun, WorkflowStep, utcnow
from ..security import SYSTEM, Actor
from ..system import get_state


class WaitingForApproval(Exception):
    def __init__(self, approval: Approval):
        super().__init__(f"waiting for approval {approval.id}")
        self.approval = approval


class Rejected(Exception):
    pass


class PermanentStepError(Exception):
    """Do not retry (bad input, policy violation)."""


@dataclass
class StepContext:
    session: Session
    run: WorkflowRun
    step: WorkflowStep

    @property
    def input(self) -> dict:
        return self.run.input

    def out(self, step_name: str) -> dict:
        return self.run.context.get(step_name, {})

    def agent(self, name: str, payload: dict | BaseModel) -> dict:
        ag = AGENTS[name](AgentContext(self.session, self.run.id, self.step.id, self.run.created_by))
        return ag.execute(payload).model_dump(mode="json")

    def gate(
        self,
        *,
        subject_type: str,
        subject_id: str,
        action: str,
        summary: str,
        requested_by: str,
        payload: dict | None = None,
        on_rejected: Callable[[Approval], None] | None = None,
    ) -> dict:
        """Idempotent approval gate: creates the approval once, then passes when a human approves it."""
        apr = approvals.request(
            self.session,
            subject_type=subject_type,
            subject_id=subject_id,
            action=action,
            requested_by=Actor("agent", requested_by),
            initiated_by=self.run.created_by.split(":", 1)[-1],
            run_id=self.run.id,
            step_id=self.step.id,
            summary=summary,
            payload=payload,
        )
        self.step.approval_id = apr.id
        if apr.status == "approved":
            return {"approval_id": apr.id, "decided_by": apr.decided_by, "status": "approved"}
        if apr.status in ("rejected", "expired"):
            if on_rejected:
                on_rejected(apr)
            raise Rejected(f"{action} {apr.status} by {apr.decided_by}: {apr.reason}")
        raise WaitingForApproval(apr)


@dataclass
class StepDef:
    name: str
    handler: Callable[[StepContext], dict]
    agent: str | None = None
    max_attempts: int = 3
    title: str = ""


@dataclass
class WorkflowDef:
    name: str
    title: str
    description: str
    steps: list[StepDef]
    input_model: type[BaseModel]
    agents: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.agents = sorted({s.agent for s in self.steps if s.agent})


REGISTRY: dict[str, WorkflowDef] = {}


def register(wf: WorkflowDef) -> WorkflowDef:
    REGISTRY[wf.name] = wf
    return wf


# ---------------------------------------------------------------- lifecycle


def start_run(
    session: Session, workflow: str, inp: dict, actor: Actor, idempotency_key: str | None = None
) -> WorkflowRun:
    wf = REGISTRY[workflow]
    validated = wf.input_model.model_validate(inp).model_dump(mode="json")
    if idempotency_key:
        existing = session.scalars(select(WorkflowRun).where(WorkflowRun.idempotency_key == idempotency_key)).first()
        if existing:
            return existing
    run = WorkflowRun(
        workflow=workflow,
        status="running",
        input=validated,
        context={},
        idempotency_key=idempotency_key,
        created_by=actor.label,
    )
    session.add(run)
    session.flush()
    for i, sd in enumerate(wf.steps):
        session.add(
            WorkflowStep(
                run_id=run.id,
                idx=i,
                name=sd.name,
                agent=sd.agent,
                max_attempts=sd.max_attempts,
                idempotency_key=f"{run.id}:{sd.name}",
            )
        )
    audit.record(
        session,
        actor,
        "workflow.started",
        "workflow_run",
        run.id,
        after={"workflow": workflow, "input": validated},
        run_id=run.id,
    )
    session.commit()
    return run


def _steps(session: Session, run_id: str) -> list[WorkflowStep]:
    return list(session.scalars(select(WorkflowStep).where(WorkflowStep.run_id == run_id).order_by(WorkflowStep.idx)))


def _acquire(session: Session, run_id: str) -> str | None:
    token = uuid.uuid4().hex[:12]
    now = utcnow()
    res = session.execute(
        update(WorkflowRun)
        .where(WorkflowRun.id == run_id, or_(WorkflowRun.lease_until.is_(None), WorkflowRun.lease_until < now))
        .values(lease_owner=token, lease_until=now + timedelta(seconds=get_settings().run_lease_seconds))
    )
    session.commit()
    return token if res.rowcount == 1 else None


def _release(session: Session, run_id: str, token: str) -> None:
    session.execute(
        update(WorkflowRun)
        .where(WorkflowRun.id == run_id, WorkflowRun.lease_owner == token)
        .values(lease_owner=None, lease_until=None)
    )
    session.commit()


def tick(session: Session, run: WorkflowRun, now: datetime | None = None) -> str:
    """Execute at most one step. Returns a short outcome string. Caller must hold the lease."""
    now = now or utcnow()
    if get_state(session).emergency_stop:
        return "halted:emergency_stop"
    if run.status in ("completed", "failed", "cancelled", "paused"):
        return f"noop:{run.status}"
    wf = REGISTRY[run.workflow]
    steps = _steps(session, run.id)
    step = next((s for s in steps if s.status not in ("succeeded", "skipped")), None)
    if step is None:
        run.status = "completed"
        run.updated_at = now
        audit.record(session, SYSTEM, "workflow.completed", "workflow_run", run.id, run_id=run.id)
        session.commit()
        return "completed"
    if step.status == "failed":
        return "noop:failed_step"
    if step.status == "retry_wait" and step.next_attempt_at and step.next_attempt_at > now:
        return "backoff"
    if step.status == "running":  # we hold the lease, so the previous holder died mid-step
        audit.record(
            session,
            SYSTEM,
            "workflow.step_recovered",
            "workflow_step",
            step.id,
            run_id=run.id,
            step_id=step.id,
            severity="warn",
        )
    sd = next(s for s in wf.steps if s.name == step.name)
    if step.status != "waiting_approval":
        step.attempts += 1
    step.status = "running"
    step.started_at = step.started_at or now
    session.commit()
    ctx = StepContext(session, run, step)
    try:
        output = sd.handler(ctx) or {}
    except WaitingForApproval as w:
        # Gate steps have no side effects other than the (idempotent) approval request, so we keep it.
        step.status = "waiting_approval"
        step.approval_id = w.approval.id
        run.status = "waiting_approval"
        run.updated_at = now
        session.commit()
        return "waiting_approval"
    except Rejected as r:
        session.commit()  # keep side effects of on_rejected
        step.status = "failed"
        step.error = str(r)
        run.status = "cancelled"
        run.error = str(r)
        run.updated_at = now
        audit.record(
            session,
            SYSTEM,
            "workflow.cancelled_by_rejection",
            "workflow_run",
            run.id,
            after={"reason": str(r)},
            run_id=run.id,
            step_id=step.id,
            severity="warn",
        )
        session.commit()
        return "cancelled"
    except Exception as e:  # noqa: BLE001
        session.rollback()
        step = session.get(WorkflowStep, step.id)
        run = session.get(WorkflowRun, run.id)
        assert step is not None and run is not None
        msg = f"{e.__class__.__name__}: {e}"
        step.error = msg
        permanent = isinstance(
            e,
            (PermanentStepError, PermissionError, approvals.ApprovalError, LookupError, AgentIncomplete, AdapterError),
        )
        if step.attempts >= step.max_attempts or permanent:
            step.status = "failed"
            step.finished_at = now
            run.status = "failed"
            run.error = f"{step.name}: {msg}"
            session.add(
                ErrorQueueItem(
                    run_id=run.id,
                    step_id=step.id,
                    workflow=run.workflow,
                    step_name=step.name,
                    error=msg + "\n" + traceback.format_exc(limit=3),
                    attempts=step.attempts,
                )
            )
            audit.record(
                session,
                SYSTEM,
                "workflow.step_failed",
                "workflow_step",
                step.id,
                after={"error": msg, "attempts": step.attempts},
                run_id=run.id,
                step_id=step.id,
                severity="error",
            )
            session.commit()
            return "failed"
        delay = get_settings().retry_base_seconds * (2 ** (step.attempts - 1))
        step.status = "retry_wait"
        step.next_attempt_at = now + timedelta(seconds=delay)
        audit.record(
            session,
            SYSTEM,
            "workflow.step_retry_scheduled",
            "workflow_step",
            step.id,
            after={"error": msg, "attempt": step.attempts, "delay_s": delay},
            run_id=run.id,
            step_id=step.id,
            severity="warn",
        )
        session.commit()
        return "retry_scheduled"
    step.status = "succeeded"
    step.output = output
    step.error = None
    step.finished_at = utcnow()
    run.context = {**run.context, step.name: output}
    run.updated_at = utcnow()
    if run.status == "waiting_approval":
        run.status = "running"
    audit.record(
        session,
        SYSTEM,
        "workflow.step_succeeded",
        "workflow_step",
        step.id,
        after={"step": step.name, "agent": step.agent},
        run_id=run.id,
        step_id=step.id,
    )
    session.commit()
    return "step_succeeded"


def advance(session: Session, run_id: str, budget_seconds: float | None = None, now: datetime | None = None) -> dict:
    """Run steps until the run blocks or the time budget is used up. Never waits on backoff; callers poll."""
    budget = get_settings().tick_budget_seconds if budget_seconds is None else budget_seconds
    run = session.get(WorkflowRun, run_id)
    if run is None:
        raise LookupError("run not found")
    token = _acquire(session, run_id)
    if token is None:
        return {"run_id": run_id, "outcome": "busy", "status": run.status, "ticks": 0}
    started, ticks, outcome = time.monotonic(), 0, "noop"
    try:
        while True:
            session.refresh(run)
            outcome = tick(session, run, now)
            ticks += 1
            if outcome != "step_succeeded":
                break
            if time.monotonic() - started >= budget:
                outcome = "budget_exhausted"
                break
    finally:
        _release(session, run_id, token)
    session.refresh(run)
    return {"run_id": run_id, "outcome": outcome, "status": run.status, "ticks": ticks}


def resume_after_decision(session: Session, approval: Approval) -> dict | None:
    if not approval.run_id:
        return None
    run = session.get(WorkflowRun, approval.run_id)
    if run is None or run.status not in ("waiting_approval",):
        return None
    run.status = "running"
    session.commit()
    return advance(session, run.id)


def pause(session: Session, run_id: str, actor: Actor) -> WorkflowRun:
    run = session.get(WorkflowRun, run_id)
    if run is None:
        raise LookupError("run not found")
    if run.status in ("running", "waiting_approval"):
        audit.record(
            session, actor, "workflow.paused", "workflow_run", run.id, before={"status": run.status}, run_id=run.id
        )
        run.context = {**run.context, "_paused_from": run.status}
        run.status = "paused"
        session.commit()
    return run


def resume(session: Session, run_id: str, actor: Actor) -> dict:
    run = session.get(WorkflowRun, run_id)
    if run is None:
        raise LookupError("run not found")
    if run.status == "paused":
        prev = run.context.get("_paused_from", "running")
        run.status = "waiting_approval" if prev == "waiting_approval" else "running"
        audit.record(
            session, actor, "workflow.resumed", "workflow_run", run.id, after={"status": run.status}, run_id=run.id
        )
        session.commit()
        if run.status == "waiting_approval":
            # the approval may have been decided while paused
            st = next((s for s in _steps(session, run.id) if s.status == "waiting_approval"), None)
            apr = session.get(Approval, st.approval_id) if st and st.approval_id else None
            if apr and apr.status != "pending":
                run.status = "running"
                session.commit()
    if run.status == "running":
        return advance(session, run.id)
    return {"run_id": run.id, "outcome": "noop", "status": run.status, "ticks": 0}


def cancel(session: Session, run_id: str, actor: Actor, reason: str) -> WorkflowRun:
    run = session.get(WorkflowRun, run_id)
    if run is None:
        raise LookupError("run not found")
    if run.status not in ("completed", "failed", "cancelled"):
        audit.record(
            session,
            actor,
            "workflow.cancelled",
            "workflow_run",
            run.id,
            before={"status": run.status},
            after={"reason": reason},
            run_id=run.id,
            severity="warn",
        )
        run.status = "cancelled"
        run.error = reason
        session.commit()
    return run


def retry_error(session: Session, error_id: str, actor: Actor) -> dict:
    item = session.get(ErrorQueueItem, error_id)
    if item is None or item.status != "open":
        raise LookupError("open error item not found")
    step = session.get(WorkflowStep, item.step_id)
    run = session.get(WorkflowRun, item.run_id)
    assert step is not None and run is not None
    step.status, step.attempts, step.next_attempt_at, step.error = "pending", 0, None, None
    run.status, run.error = "running", None
    item.status, item.resolved_by = "retried", actor.label
    audit.record(session, actor, "workflow.error_retried", "error_queue", item.id, run_id=run.id, step_id=step.id)
    session.commit()
    return advance(session, run.id)
