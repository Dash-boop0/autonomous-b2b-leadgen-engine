"""Workflow engine (retries, backoff, error queue, pause/resume, leases, crash recovery), approval engine rules,
agent tool scoping and completion criteria, RBAC and security helpers, migrations/bootstrap."""

from datetime import timedelta

import pytest
from pydantic import BaseModel
from sqlalchemy import inspect, select

from app import approvals, bootstrap, db
from app.agents import AGENTS, AgentContext
from app.agents.base import AgentIncomplete, AgentOutput, BaseAgent, ToolNotPermitted
from app.config import normalise_db_url
from app.models import Approval, ErrorQueueItem, User, WorkflowRun, WorkflowStep, utcnow
from app.security import Actor, agent_actor, issue_token, read_token
from app.workflows import engine as wf
from app.workflows.engine import StepDef, WorkflowDef


class _In(BaseModel):
    pass


CALLS = {"flaky": 0}


def _flaky(ctx):
    CALLS["flaky"] += 1
    if CALLS["flaky"] < 3:
        raise RuntimeError("transient boom")
    return {"ok": True}


def _always_fail(ctx):
    raise RuntimeError("permanent boom")


wf.register(WorkflowDef("test_flaky", "Flaky", "test", [StepDef("flaky", _flaky, max_attempts=3)], _In))
wf.register(WorkflowDef("test_fail", "Fail", "test", [StepDef("fail", _always_fail, max_attempts=2)], _In))
wf.register(
    WorkflowDef("test_two", "Two", "test", [StepDef("a", lambda c: {"a": 1}), StepDef("b", lambda c: {"b": 2})], _In)
)

OP = Actor("human", "usr_test_operator", "operator")


def test_retries_with_backoff_then_success(session):
    CALLS["flaky"] = 0
    run = wf.start_run(session, "test_flaky", {}, OP)
    r = wf.advance(session, run.id)
    assert r["outcome"] == "retry_scheduled"
    step = session.scalars(select(WorkflowStep).where(WorkflowStep.run_id == run.id)).one()
    assert step.status == "retry_wait" and step.attempts == 1 and step.next_attempt_at is not None
    later = utcnow() + timedelta(seconds=60)
    assert wf.advance(session, run.id, now=later)["outcome"] == "retry_scheduled"
    assert wf.advance(session, run.id, now=later + timedelta(seconds=60))["status"] == "completed"
    assert CALLS["flaky"] == 3


def test_backoff_is_respected(session, monkeypatch):
    monkeypatch.setattr(wf.get_settings(), "retry_base_seconds", 30.0)
    CALLS["flaky"] = 0
    run = wf.start_run(session, "test_flaky", {}, OP)
    wf.advance(session, run.id)
    assert wf.advance(session, run.id)["outcome"] == "backoff"
    assert CALLS["flaky"] == 1


def test_exhausted_retries_go_to_error_queue_and_can_be_retried(session):
    run = wf.start_run(session, "test_fail", {}, OP)
    wf.advance(session, run.id)
    out = wf.advance(session, run.id, now=utcnow() + timedelta(seconds=10))
    assert out["status"] == "failed"
    err = session.scalars(select(ErrorQueueItem).where(ErrorQueueItem.run_id == run.id)).one()
    assert err.attempts == 2 and "permanent boom" in err.error
    res = wf.retry_error(session, err.id, OP)
    assert res["status"] in ("running", "failed")
    assert session.get(ErrorQueueItem, err.id).status == "retried"


def test_pause_resume_cancel_and_idempotent_start(session):
    run = wf.start_run(session, "test_two", {}, OP, idempotency_key="k1")
    assert wf.start_run(session, "test_two", {}, OP, idempotency_key="k1").id == run.id
    wf.pause(session, run.id, OP)
    assert wf.advance(session, run.id)["outcome"] == "noop:paused"
    assert wf.resume(session, run.id, OP)["status"] == "completed"
    run2 = wf.start_run(session, "test_two", {}, OP)
    assert wf.cancel(session, run2.id, OP, "test").status == "cancelled"


def test_lease_prevents_concurrent_execution_and_crash_recovery(session):
    run = wf.start_run(session, "test_two", {}, OP)
    token = wf._acquire(session, run.id)
    assert token and wf.advance(session, run.id)["outcome"] == "busy"
    # simulate a crashed worker: step left 'running', lease expired
    step = session.scalars(select(WorkflowStep).where(WorkflowStep.run_id == run.id, WorkflowStep.idx == 0)).one()
    step.status = "running"
    r = session.get(WorkflowRun, run.id)
    r.lease_until = utcnow() - timedelta(seconds=1)
    session.commit()
    assert wf.advance(session, run.id)["status"] == "completed"


def test_supervisor_recovers_stale_running_step(session):
    run = wf.start_run(session, "test_two", {}, OP)
    step = session.scalars(select(WorkflowStep).where(WorkflowStep.run_id == run.id, WorkflowStep.idx == 0)).one()
    step.status, step.started_at = "running", utcnow() - timedelta(hours=1)
    session.commit()
    out = AGENTS["workflow_supervisor"](AgentContext(session)).execute({"stale_after_seconds": 60})
    assert step.id in out.recovered_steps
    assert session.get(WorkflowStep, step.id).status == "pending"


def test_approval_engine_rules(session):
    apr = approvals.request(
        session,
        subject_type="campaign",
        subject_id="cmp_x",
        action="campaign.activate",
        requested_by=agent_actor("outreach_operations"),
        initiated_by="usr_op",
    )
    with pytest.raises(approvals.ApprovalError, match="Agents"):
        approvals.decide(session, apr.id, agent_actor("follow_up"), "approved", None)
    with pytest.raises(approvals.ApprovalError, match="cannot decide"):
        approvals.decide(session, apr.id, Actor("human", "usr_v", "viewer"), "approved", None)
    with pytest.raises(approvals.ApprovalError, match="Four-eyes"):
        approvals.decide(session, apr.id, Actor("human", "usr_op", "admin"), "approved", None)
    approvals.decide(session, apr.id, Actor("human", "usr_rv", "reviewer"), "approved", "ok")
    assert approvals.is_approved(session, "campaign", "cmp_x", "campaign.activate")
    with pytest.raises(approvals.ApprovalError, match="already"):
        approvals.decide(session, apr.id, Actor("human", "usr_rv2", "reviewer"), "rejected", "late")
    self_apr = approvals.request(
        session,
        subject_type="icp_version",
        subject_id="icp_y",
        action="icp.update",
        requested_by=Actor("human", "usr_rv", "reviewer"),
        initiated_by=None,
    )
    with pytest.raises(approvals.ApprovalError, match="Self-approval"):
        approvals.decide(session, self_apr.id, Actor("human", "usr_rv", "reviewer"), "approved", None)
    assert session.scalars(select(Approval).where(Approval.id == apr.id)).one().decided_by == "usr_rv"


def test_all_twenty_agents_typed_and_wired_into_workflows():
    assert len(AGENTS) == 20
    groups = {a.group for a in AGENTS.values()}
    assert groups == {"Market & strategy", "Discovery & data", "Campaign & execution", "Measurement & governance"}
    wired = {a for w in wf.REGISTRY.values() for a in w.agents}
    assert set(AGENTS) <= wired
    for a in AGENTS.values():
        d = a.describe()
        assert d["input_schema"]["properties"] is not None and "status" in d["output_schema"]["properties"]
        assert d["completion_criteria"]


class _Rogue(BaseAgent):
    name, group, description = "rogue", "test", "tries a tool it does not own"
    tools = ("llm.generate",)
    input_model, output_model = _In, AgentOutput
    completion_criteria = "never"

    def run(self, inp):
        self.tool("outreach.dispatch", campaign=None)
        return AgentOutput()


class _LowConfidence(_Rogue):
    name = "low_conf"

    def run(self, inp):
        return AgentOutput(confidence=0.2)


class _Incomplete(_Rogue):
    name = "incomplete"

    def run(self, inp):
        return AgentOutput()

    def check_completion(self, out):
        return ["required thing missing"]


def test_agent_tool_scope_confidence_and_completion(session):
    with pytest.raises(ToolNotPermitted):
        _Rogue(AgentContext(session)).execute({})
    assert _LowConfidence(AgentContext(session)).execute({}).status == "needs_review"
    with pytest.raises(AgentIncomplete):
        _Incomplete(AgentContext(session)).execute({})


def test_agents_cannot_hold_privileged_permissions():
    a = agent_actor("any")
    for p in ("approvals.decide", "system.stop", "system.reset", "sources.configure", "contacts.erase"):
        assert not a.can(p)


def test_tokens_are_signed_and_roles_come_from_db(session, client):
    u = session.scalars(select(User).where(User.username == "viewer")).one()
    t = issue_token(u)
    assert read_token(t)["sub"] == u.id
    body, sig = t.split(".")
    assert read_token(body + "." + sig[:-2] + "xx") is None
    assert client.get("/api/dashboard").status_code == 401
    assert client.get("/api/dashboard", headers={"Authorization": "Bearer nope"}).status_code == 401
    h = {"Authorization": f"Bearer {t}"}
    assert client.get("/api/dashboard", headers=h).status_code == 200
    assert client.post("/api/workflows/governance_sweep/runs", headers=h, json={"input": {}}).status_code == 403
    assert client.post("/api/admin/reset-demo", headers=h, json={}).status_code == 403


def test_reset_demo_is_admin_only_and_audited(api):
    api.approve("icp.activate")
    assert api.post("/api/admin/reset-demo", user="operator", json={}).status_code == 403
    r = api.ok(api.post("/api/admin/reset-demo", user="admin", json={"profile": "basic"}))
    assert r["reset"] and r["token"]
    api.tokens.clear()
    assert len(api.ok(api.get("/api/approvals?status=pending"))) == 1, "back to the seeded state"
    assert api.ok(api.get("/api/audit?action=demo.reset"))["total"] == 1


def test_governance_sweep_workflow(api):
    run = api.run("governance_sweep", {}, user="operator")
    assert run["status"] == "completed"
    assert [s["agent"] for s in run["steps"]] == ["workflow_supervisor", "compliance_governance"]


def test_migrations_match_models_and_bootstrap_is_idempotent(fresh_db):
    insp = inspect(db.engine())
    assert set(db.Base.metadata.tables) <= set(insp.get_table_names())
    assert bootstrap.migrate_and_seed() is False, "second bootstrap does not re-seed"
    s = db.session_factory()()
    try:
        assert len(s.scalars(select(User)).all()) == 4
    finally:
        s.close()


def test_database_url_normalisation():
    assert (
        normalise_db_url("postgres://u:p@ep-1.neon.tech/db")
        == "postgresql+psycopg://u:p@ep-1.neon.tech/db?sslmode=require"
    )
    assert normalise_db_url("postgresql://u:p@db:5432/x") == "postgresql+psycopg://u:p@db:5432/x"
    assert normalise_db_url("postgresql://u:p@localhost/x") == "postgresql+psycopg://u:p@localhost/x"
    assert "sslmode=verify-full" in normalise_db_url("postgres://u:p@h.neon.tech/db?sslmode=verify-full")
    assert normalise_db_url("sqlite:///x.db") == "sqlite:///x.db"


def test_vercel_entry_point_exposes_app():
    import importlib

    mod = importlib.import_module("api.index")
    assert any(getattr(r, "path", None) == "/health" for r in mod.app.routes)
