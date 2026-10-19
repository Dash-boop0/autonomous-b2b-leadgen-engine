"""Health, demo auth, system controls (emergency stop, simulated clock, reset demo), dashboard, agents and audit."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .. import audit, bootstrap
from ..agents import AGENTS
from ..config import get_settings
from ..db import engine, get_session
from ..llm import get_llm
from ..models import (
    Account,
    Approval,
    AuditEvent,
    Campaign,
    Contact,
    ErrorQueueItem,
    ICPVersion,
    Lead,
    Reply,
    SendQueueItem,
    User,
    WorkflowRun,
)
from ..schemas import AuditEvent as AuditOut
from ..security import Actor, current_actor, issue_token, require, verify_password
from ..system import get_state, set_stop
from ..workflows.engine import REGISTRY

router = APIRouter()


def _health() -> dict:
    s = get_settings()
    out = {"status": "ok", "app_mode": s.app_mode, "send_mode": "dry_run", "llm_provider": get_llm().name}
    try:
        bootstrap.ensure_ready()
        with engine().connect() as c:
            c.execute(text("SELECT 1"))
            seeded = (c.execute(text("SELECT COUNT(*) FROM users")).scalar() or 0) > 0
        out["db"] = {"status": "ok", "dialect": engine().dialect.name, "seeded": seeded}
    except Exception as e:  # noqa: BLE001
        out["status"] = "degraded"
        out["db"] = {"status": "error", "error": e.__class__.__name__}
    return out


@router.get("/health")
def health() -> dict:
    return _health()


@router.get("/api/health")
def api_health() -> dict:
    return _health()


# ---------------------------------------------------------------- auth


class LoginIn(BaseModel):
    username: str
    password: str


def _user_out(u: User) -> dict:
    return {"id": u.id, "username": u.username, "display_name": u.display_name, "role": u.role}


@router.post("/api/auth/login")
def login(body: LoginIn, session: Session = Depends(get_session)) -> dict:
    u = session.scalars(select(User).where(User.username == body.username)).first()
    if not u or not u.active or not verify_password(body.password, u.password_hash):
        raise HTTPException(401, "Invalid username or password")
    audit.record(session, Actor("human", u.id, u.role), "auth.login", "user", u.id)
    session.commit()
    return {"token": issue_token(u), "user": _user_out(u)}


@router.get("/api/auth/demo-users")
def demo_users(session: Session = Depends(get_session)) -> list[dict]:
    """Demo only: lists the seeded accounts for the role switcher (shared password is documented)."""
    return [_user_out(u) for u in session.scalars(select(User).order_by(User.username))]


@router.get("/api/auth/me")
def me(actor: Actor = Depends(current_actor), session: Session = Depends(get_session)) -> dict:
    u = session.get(User, actor.actor_id)
    from ..security import ROLE_PERMISSIONS

    return {**_user_out(u), "permissions": sorted(ROLE_PERMISSIONS[u.role])}  # type: ignore[arg-type]


# ---------------------------------------------------------------- system


@router.get("/api/system")
def system_state(session: Session = Depends(get_session), _: Actor = Depends(require("read"))) -> dict:
    st = get_state(session)
    s = get_settings()
    llm = get_llm()
    return {
        "emergency_stop": st.emergency_stop,
        "stop_reason": st.stop_reason,
        "stop_set_by": st.stop_set_by,
        "stop_set_at": st.stop_set_at.isoformat() if st.stop_set_at else None,
        "send_mode": "dry_run",
        "sim_clock_offset_days": st.sim_clock_offset_days,
        "app_mode": s.app_mode,
        "seeded_profile": st.seeded_profile,
        "providers": {
            "llm": {"name": llm.name, "mock": llm.mock},
            "email_sender": {"name": "dry-run-outbox", "mock": True},
            "dns": {"name": s.dns_provider, "mock": s.dns_provider == "mock"},
            "verifier": {"name": "mock-verifier", "mock": True},
            "crm": {"name": "mock-crm", "mock": True},
        },
    }


class StopIn(BaseModel):
    on: bool
    reason: str | None = None


@router.post("/api/system/stop")
def toggle_stop(
    body: StopIn, session: Session = Depends(get_session), actor: Actor = Depends(require("system.stop"))
) -> dict:
    if body.on and not (body.reason or "").strip():
        raise HTTPException(422, "A reason is required to engage the emergency stop")
    st = set_stop(session, actor, body.on, body.reason)
    session.commit()
    return {"emergency_stop": st.emergency_stop, "stop_reason": st.stop_reason}


class ClockIn(BaseModel):
    days: int = Field(ge=1, le=30)


@router.post("/api/system/sim-clock")
def advance_clock(
    body: ClockIn, session: Session = Depends(get_session), actor: Actor = Depends(require("events.simulate"))
) -> dict:
    st = get_state(session)
    before = st.sim_clock_offset_days
    st.sim_clock_offset_days += body.days
    audit.record(
        session,
        actor,
        "system.sim_clock_advanced",
        "system",
        "1",
        before={"days": before},
        after={"days": st.sim_clock_offset_days},
    )
    session.commit()
    return {"sim_clock_offset_days": st.sim_clock_offset_days}


class ResetIn(BaseModel):
    profile: str = Field("basic", pattern="^(basic|full)$")


@router.post("/api/admin/reset-demo")
def reset_demo(
    body: ResetIn, actor: Actor = Depends(require("system.reset")), session: Session = Depends(get_session)
) -> dict:
    username = session.get(User, actor.actor_id).username  # type: ignore[union-attr]
    session.close()
    out = bootstrap.reset_demo(body.profile)
    from ..db import session_factory

    s2 = session_factory()()
    try:
        u = s2.scalars(select(User).where(User.username == username)).first()
        who = Actor("human", u.id, u.role) if u else actor
        audit.record(
            s2, who, "demo.reset", "system", "1", after={"profile": body.profile, "by": username}, severity="warn"
        )
        s2.commit()
        token = issue_token(u) if u else None
    finally:
        s2.close()
    return {"reset": True, "profile": body.profile, **out, "token": token}


# ---------------------------------------------------------------- dashboard


@router.get("/api/dashboard")
def dashboard(session: Session = Depends(get_session), _: Actor = Depends(require("read"))) -> dict:
    def count(model, *where) -> int:
        q = select(func.count()).select_from(model)
        for w in where:
            q = q.where(w)
        return session.scalar(q) or 0

    icp = session.scalars(select(ICPVersion).where(ICPVersion.status == "approved")).first()
    camp = session.scalars(select(Campaign).order_by(Campaign.created_at.desc())).first()
    checklist = [
        {"key": "icp", "label": "Approve an ICP", "done": icp is not None, "href": "/approvals"},
        {"key": "accounts", "label": "Import & score accounts", "done": count(Account) > 0, "href": "/sources"},
        {"key": "contacts", "label": "Discover & verify contacts", "done": count(Contact) > 0, "href": "/accounts"},
        {
            "key": "campaign",
            "label": "Create a campaign & review drafts",
            "done": camp is not None,
            "href": "/campaigns",
        },
        {
            "key": "approved",
            "label": "Approve the campaign (reviewer)",
            "done": bool(camp and camp.status in ("approved", "active", "completed")),
            "href": "/approvals",
        },
        {
            "key": "sent",
            "label": "Run dry-run send + simulated inbox",
            "done": count(SendQueueItem, SendQueueItem.status == "sent_simulated") > 0,
            "href": "/campaigns",
        },
        {"key": "replies", "label": "Triage replies", "done": count(Reply) > 0, "href": "/replies"},
        {
            "key": "improve",
            "label": "Review analytics & ICP recommendations",
            "done": count(ICPVersion) > 1,
            "href": "/analytics",
        },
    ]
    runs = session.scalars(select(WorkflowRun).order_by(WorkflowRun.created_at.desc()).limit(6)).all()
    return {
        "counts": {
            "accounts": count(Account),
            "contacts": count(Contact),
            "leads": count(Lead),
            "qualified_leads": count(Lead, Lead.stage == "qualified"),
            "pending_approvals": count(Approval, Approval.status == "pending"),
            "open_errors": count(ErrorQueueItem, ErrorQueueItem.status == "open"),
            "replies_needing_review": count(Reply, Reply.status.in_(["needs_review", "escalated"])),
            "sent_simulated": count(SendQueueItem, SendQueueItem.status == "sent_simulated"),
        },
        "active_icp": {"id": icp.id, "version": icp.version, "name": icp.name} if icp else None,
        "latest_campaign": {"id": camp.id, "name": camp.name, "status": camp.status} if camp else None,
        "checklist": checklist,
        "recent_runs": [
            {
                "id": r.id,
                "workflow": r.workflow,
                "title": REGISTRY[r.workflow].title,
                "status": r.status,
                "created_at": r.created_at.isoformat(),
            }
            for r in runs
        ],
    }


# ---------------------------------------------------------------- agents & audit


@router.get("/api/agents")
def agents(_: Actor = Depends(require("read"))) -> list[dict]:
    used: dict[str, list[str]] = {}
    for wf in REGISTRY.values():
        for a in wf.agents:
            used.setdefault(a, []).append(wf.title)
    return [{**cls.describe(), "workflows": used.get(name, [])} for name, cls in AGENTS.items()]


@router.get("/api/audit")
def audit_log(
    session: Session = Depends(get_session),
    _: Actor = Depends(require("read")),
    action: str | None = None,
    actor_type: str | None = None,
    run_id: str | None = None,
    subject_id: str | None = None,
    severity: str | None = None,
    limit: int = Query(100, le=500),
    offset: int = 0,
) -> dict:
    q = select(AuditEvent)
    if action:
        q = q.where(AuditEvent.action.like(f"%{action}%"))
    if actor_type:
        q = q.where(AuditEvent.actor_type == actor_type)
    if run_id:
        q = q.where(AuditEvent.run_id == run_id)
    if subject_id:
        q = q.where(AuditEvent.subject_id == subject_id)
    if severity:
        q = q.where(AuditEvent.severity == severity)
    total = session.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = session.scalars(q.order_by(AuditEvent.at.desc(), AuditEvent.id.desc()).limit(limit).offset(offset)).all()
    return {
        "total": total,
        "items": [{**AuditOut.model_validate(r).model_dump(mode="json"), "severity": r.severity} for r in rows],
    }
