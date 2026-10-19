"""Acceptance tests from the brief, one named test per requirement."""

from sqlalchemy import select

from app import db
from app.config import get_settings
from app.models import Contact, MessageEvent, SendQueueItem
from tests.conftest import build_pipeline


def test_clean_setup_without_paid_credentials(api):
    s = get_settings()
    assert s.llm_provider == "mock" and s.anthropic_api_key is None
    system = api.ok(api.get("/api/system"))
    assert system["send_mode"] == "dry_run"
    assert all(p["mock"] for p in system["providers"].values())
    dash = api.ok(api.get("/api/dashboard"))
    assert dash["counts"]["pending_approvals"] == 1, "seed stops at the ICP approval gate"
    assert len(api.ok(api.get("/api/agents"))) == 20


def test_approve_icp_import_dedup_and_score(api):
    api.approve("icp.activate")
    icp = api.active_icp()
    up = api.upload("accounts")
    run = api.run(
        "icp_to_accounts",
        {"icp_version_id": icp["id"], "adapters": ["csv_upload", "mock_registry"], "csv_upload_id": up["id"]},
    )
    assert run["status"] == "completed"
    dedup = next(s for s in run["steps"] if s["name"] == "dedup")["output"]
    assert dedup["merged"] >= 4, "CSV duplicates and CSV/registry overlaps are merged"
    accounts = api.ok(api.get("/api/accounts"))
    domains = [a["domain"] for a in accounts if a["domain"]]
    assert len(domains) == len(set(domains)), "no duplicate domains"
    bright = next(a for a in accounts if a["domain"] == "brightlane.example.com")
    assert len(bright["source_ids"]) == 3, "two CSV rows + one registry record merged into one account"
    detail = api.ok(api.get(f"/api/accounts/{bright['id']}"))
    q = detail["qualification"]
    assert q["model_version"].startswith("icp-v1-") and len(q["criteria"]) == 8
    assert abs(sum(c["contribution"] for c in q["criteria"]) - q["total_score_0_100"]) < 0.2
    assert any(e["kind"] == "merge" for e in detail["evidence"])
    tiers = {a["name"]: a["tier"] for a in accounts}
    assert tiers["Redwater Gaming Ltd"] == "disqualified"  # excluded industry
    assert tiers["Valemont Analytics"] == "disqualified"  # competitor
    assert tiers["Yarrow Medical Supplies"] == "disqualified"  # outside served countries
    assert tiers["Mossgate Retail Group"] == "disqualified"  # suppressed domain
    # re-running the same import is idempotent
    again = api.run(
        "icp_to_accounts",
        {"icp_version_id": icp["id"], "adapters": ["csv_upload", "mock_registry"], "csv_upload_id": up["id"]},
    )
    assert next(s for s in again["steps"] if s["name"] == "dedup")["output"]["created"] == 0
    assert len(api.ok(api.get("/api/accounts"))) == len(accounts)


def test_contacts_buyer_roles_and_honest_verification_levels(api):
    build_pipeline(api)
    contacts = {c["email"]: c for c in api.ok(api.get("/api/contacts")) if c["email"]}
    assert contacts["ava.thornton@brightlane.example.com"]["buyer_role"] == "economic_buyer"
    assert contacts["rohan.mehta@brightlane.example.com"]["buyer_role"] == "champion"
    assert contacts["cian.walsh@copperfield-wholesale.example.com"]["buyer_role"] == "technical"
    assert contacts["ellie.marsh@brightlane.example.com"]["buyer_role"] == "unknown"
    assert contacts["tom.reed@@quayside.example.net"]["email_status"] == "syntax_invalid"
    assert contacts["pieter.janssen@saltmarsh.example.com"]["email_status"] == "syntax_valid"  # no MX
    assert (
        contacts["cian.walsh@copperfield-wholesale.example.com"]["email_status"] == "domain_has_mx"
    )  # provider unknown
    ava = contacts["ava.thornton@brightlane.example.com"]
    assert ava["email_status"] == "provider_verified"
    prov = next(c for c in ava["verification_checks"] if c["check"] == "provider")
    assert prov["mock"] is True and prov["provider"] == "mock-verifier"
    assert not any(c["email_status"] == "deliverable_confirmed" for c in contacts.values()), (
        "verification alone never claims deliverability"
    )
    assert contacts["liam.foster@ashdown-haulage.example.com"]["lead_stage"] == "disqualified"  # provider: invalid


def test_evidence_backed_drafts_and_simulated_campaign(api):
    ids = build_pipeline(api)
    drafts = api.ok(api.get(f"/api/campaigns/{ids['campaign_id']}"))["drafts"]
    grounded = [d for d in drafts if d["personalisation_level"] == "evidence_grounded"]
    assert grounded and all(d["evidence"] and d["evidence_ids"] for d in grounded)
    for d in grounded:
        assert any(e["claim"].split(": ", 1)[-1][:30] in d["body"] for e in d["evidence"]), "hook comes from evidence"
    generic = [d for d in drafts if d["personalisation_level"] == "generic" and d["step_no"] == 1]
    assert all(not d["evidence_ids"] for d in generic)
    assert all("unsubscribe" in d["body"].lower() for d in drafts)
    api.approve("campaign.activate")
    cyc = api.ok(api.post(f"/api/campaigns/{ids['campaign_id']}/conversation-cycle"))
    assert cyc["run"]["status"] == "completed"
    sent = [
        q for q in api.ok(api.get(f"/api/campaigns/{ids['campaign_id']}"))["queue"] if q["status"] == "sent_simulated"
    ]
    assert sent and all(q["mode"] == "dry_run" and q["provider_message_id"].startswith("dryrun-") for q in sent)


def test_opt_outs_block_sending(api):
    ids = build_pipeline(api)
    cid = ids["campaign_id"]
    # seeded opt-out (Olivia, Tidewell) never gets a lead that reaches a campaign
    contacts = {c["email"]: c for c in api.ok(api.get("/api/contacts")) if c["email"]}
    olivia = contacts["olivia.grant@tidewell.example.org"]
    assert olivia["suppressed"] and olivia["lead_stage"] == "suppressed"
    drafts = api.ok(api.get(f"/api/campaigns/{cid}"))["drafts"]
    assert not any(d["contact_email"] == olivia["email"] for d in drafts)
    # opt-out added after drafting but before approval: the queued item is blocked
    target = next(d for d in drafts if d["step_no"] == 1)
    api.ok(
        api.post(
            "/api/suppressions",
            user="operator",
            json={"email_or_domain": target["contact_email"], "reason": "opt_out", "note": "test"},
        )
    )
    api.approve("campaign.activate")
    queue = api.ok(api.get(f"/api/campaigns/{cid}"))["queue"]
    blocked = [q for q in queue if q["to_email"] == target["contact_email"]]
    assert blocked and all(q["status"] == "blocked" and "opt_out" in q["status_reason"] for q in blocked)
    # opt-out after queueing: dispatch re-checks and blocks
    other = next(q for q in queue if q["status"] == "queued")
    api.ok(
        api.post("/api/suppressions", user="reviewer", json={"email_or_domain": other["to_email"], "reason": "opt_out"})
    )
    api.ok(api.post(f"/api/campaigns/{cid}/conversation-cycle"))
    queue = api.ok(api.get(f"/api/campaigns/{cid}"))["queue"]
    assert not any(
        q["to_email"] in (target["contact_email"], other["to_email"]) and q["status"] == "sent_simulated" for q in queue
    )


def test_duplicate_events_do_not_duplicate_sends_or_followups(api):
    ids = build_pipeline(api)
    cid = ids["campaign_id"]
    api.approve("campaign.activate")
    api.ok(api.post(f"/api/campaigns/{cid}/conversation-cycle"))
    s = db.session_factory()()
    try:
        n_events = len(s.scalars(select(MessageEvent)).all())
        n_sends = len(s.scalars(select(SendQueueItem)).all())
    finally:
        s.close()
    # same simulated events again + an injected duplicate provider event
    api.ok(api.post(f"/api/campaigns/{cid}/conversation-cycle"))
    sent = next(q for q in api.ok(api.get(f"/api/campaigns/{cid}"))["queue"] if q["status"] == "sent_simulated")
    first = api.ok(
        api.post(
            "/api/events",
            json={
                "provider_event_id": "dup-1",
                "event_type": "replied",
                "send_id": sent["id"],
                "body": "Interested, let's talk",
            },
        )
    )
    second = api.ok(
        api.post(
            "/api/events",
            json={
                "provider_event_id": "dup-1",
                "event_type": "replied",
                "send_id": sent["id"],
                "body": "Interested, let's talk",
            },
        )
    )
    assert first["created"] and second["duplicate"]
    s = db.session_factory()()
    try:
        assert len(s.scalars(select(MessageEvent)).all()) == n_events + 1
        sends = s.scalars(select(SendQueueItem)).all()
        assert len(sends) == n_sends
        keys = [x.idempotency_key for x in sends]
        assert len(keys) == len(set(keys))
        per_contact_step = {(x.contact_id, x.step_no) for x in sends}
        assert len(per_contact_step) == len(sends), "one follow-up per contact and step at most"
    finally:
        s.close()
    # re-running the whole lead-to-campaign with the same idempotency key is a no-op
    again = api.ok(
        api.post(
            "/api/workflows/lead_to_campaign/runs",
            json={"input": {"campaign_id": cid}, "idempotency_key": f"l2c:{cid}"},
        )
    )
    assert again["run"]["id"] == ids["runs"][2]["id"]


def test_approvals_enforced(api):
    a = api.pending("icp.activate")
    # viewer and operator lack the permission
    assert api.post(f"/api/approvals/{a['id']}/decide", user="viewer", json={"decision": "approved"}).status_code == 403
    assert (
        api.post(f"/api/approvals/{a['id']}/decide", user="operator", json={"decision": "approved"}).status_code == 403
    )
    # downstream workflows refuse an unapproved ICP
    icp = api.ok(api.get("/api/icp"))[0]
    run = api.run("icp_to_accounts", {"icp_version_id": icp["id"], "adapters": ["mock_registry"]})
    assert run["status"] == "failed" and "approve" in run["error"]
    # rejection needs a reason
    r = api.post(f"/api/approvals/{a['id']}/decide", user="reviewer", json={"decision": "rejected"})
    assert r.status_code == 403
    # four-eyes: the admin who starts a manual ICP edit cannot approve it
    api.approve("icp.activate")
    icp = api.active_icp()
    draft = api.ok(api.post(f"/api/icp/{icp['id']}/fork", user="admin"))
    api.ok(api.post(f"/api/icp/{draft['id']}/submit", user="admin"))
    pending = api.pending("icp.update")
    r = api.post(f"/api/approvals/{pending['id']}/decide", user="admin", json={"decision": "approved"})
    assert r.status_code == 403 and "Self-approval" in r.json()["detail"]
    ok = api.post(f"/api/approvals/{pending['id']}/decide", user="reviewer", json={"decision": "approved"})
    assert ok.status_code == 200
    # decided approvals cannot be decided again
    assert (
        api.post(
            f"/api/approvals/{pending['id']}/decide", user="reviewer", json={"decision": "rejected", "reason": "x"}
        ).status_code
        == 403
    )
    # campaign queue cannot happen without approval
    ids_icp = api.active_icp()
    created = api.ok(
        api.post("/api/campaigns", json={"name": "No approval (synthetic)", "icp_version_id": ids_icp["id"]})
    )
    assert created["run"]["status"] in ("waiting_approval", "failed")
    audit = api.ok(api.get("/api/audit?action=approval.denied"))
    assert audit["total"] >= 3


def test_metrics_formulas_and_denominators(api):
    ids = build_pipeline(api)
    cid = ids["campaign_id"]
    empty = api.ok(api.get(f"/api/analytics/campaigns/{cid}"))
    assert empty["rates"]["reply_rate"]["value"] is None and empty["rates"]["reply_rate"]["denominator"] == 0
    api.approve("campaign.activate")
    api.ok(api.post(f"/api/campaigns/{cid}/conversation-cycle"))
    a = api.ok(api.get(f"/api/analytics/campaigns/{cid}"))
    c, r = a["counts"], a["rates"]
    assert r["delivery_rate"]["numerator"] == c["delivered_contacts"]
    assert r["delivery_rate"]["denominator"] == c["contacts_sent"]
    assert r["bounce_rate"]["numerator"] == c["bounced_contacts"] == 1
    assert r["reply_rate"]["denominator"] == c["delivered_contacts"]
    assert r["positive_reply_rate"]["denominator"] == c["replied_contacts"]
    assert r["reply_rate"]["value"] == round(c["replied_contacts"] / c["delivered_contacts"], 4)
    for seg in a["segments"]["industry"]:
        assert seg["rates"]["reply_rate"]["formula"] == "replied_contacts / delivered_contacts"
    assert sum(s["counts"]["contacts_sent"] for s in a["segments"]["industry"]) == c["contacts_sent"]


def test_sources_agent_actions_errors_approvals_traceable(api):
    ids = build_pipeline(api)
    # sources: every account and contact carries provenance records
    for a in api.ok(api.get("/api/accounts")):
        assert a["source_ids"]
    recs = api.ok(api.get("/api/sources/records?limit=1000"))
    assert {r["source_type"] for r in recs} >= {"csv", "registry", "licensed", "public_page"}
    # agent actions are audited with evidence ids
    agent_events = api.ok(api.get("/api/audit?actor_type=agent&limit=500"))["items"]
    assert any(e["action"] == "agent.lead_scoring.completed" and e["evidence_ids"] for e in agent_events)
    # robots-disallowed and blocklisted pages are recorded, not fetched
    run = next(r for r in api.ok(api.get("/api/runs?workflow=icp_to_accounts")))
    findings = next(s for s in run["steps"] if s["name"] == "public_web_research")["output"]["findings"]
    statuses = {f["url"]: f["status"] for f in findings}
    assert statuses["https://northgate-freight.example.com/news"] == "disallowed"
    assert statuses["https://valemont.example.com/news"] == "blocked"
    # errors: a disabled adapter fails the run into the error queue, visible and retryable
    api.ok(api.c.patch("/api/sources/mock_registry", headers=api.h("admin"), json={"enabled": False}))
    failed = api.run("icp_to_accounts", {"icp_version_id": ids["icp"]["id"], "adapters": ["mock_registry"]})
    assert failed["status"] == "failed"
    errs = api.ok(api.get("/api/errors"))
    assert errs and "disabled" in errs[0]["error"]
    api.ok(api.c.patch("/api/sources/mock_registry", headers=api.h("admin"), json={"enabled": True}))
    retried = api.ok(api.post(f"/api/errors/{errs[0]['id']}/retry"))
    assert retried["status"] == "completed"
    # approvals traceable
    trail = api.ok(api.get("/api/audit?action=approval"))["items"]
    assert {"approval.requested", "approval.approved"} <= {e["action"] for e in trail}


def test_emergency_stop_blocks_workflows_and_sends(api):
    ids = build_pipeline(api)
    cid = ids["campaign_id"]
    assert api.post("/api/system/stop", user="operator", json={"on": True, "reason": "x"}).status_code == 403
    api.ok(api.post("/api/system/stop", user="admin", json={"on": True, "reason": "incident drill"}))
    res = api.approve("campaign.activate")
    assert res["resumed"]["outcome"] == "halted:emergency_stop"
    assert not api.ok(api.get(f"/api/campaigns/{cid}"))["queue"], "nothing queued while stopped"
    run = api.ok(api.get(f"/api/runs/{res['approval']['run_id']}"))
    assert api.ok(api.post(f"/api/runs/{run['id']}/advance"))["advance"]["outcome"] == "halted:emergency_stop"
    api.ok(api.post("/api/system/stop", user="admin", json={"on": False}))
    done = api.ok(api.post(f"/api/runs/{run['id']}/advance"))
    assert done["run"]["status"] == "completed"
    s = db.session_factory()()
    try:
        assert s.scalars(select(Contact)).first() is not None
    finally:
        s.close()


def test_frequency_caps_defer_sends(api):
    from app.models import Campaign

    ids = build_pipeline(api)
    cid = ids["campaign_id"]
    api.approve("campaign.activate")
    api.ok(api.post(f"/api/campaigns/{cid}/conversation-cycle"))
    s = db.session_factory()()
    try:
        s.get(Campaign, cid).frequency_cap = {"max_per_contact_per_7d": 1, "max_sends_per_day": 50}
        s.commit()
    finally:
        s.close()
    api.ok(api.post("/api/system/sim-clock", json={"days": 3}))
    api.ok(api.post(f"/api/campaigns/{cid}/conversation-cycle"))
    queue = api.ok(api.get(f"/api/campaigns/{cid}"))["queue"]
    step2 = [q for q in queue if q["step_no"] == 2]
    assert not any(q["status"] == "sent_simulated" for q in step2), "cap of 1 per 7 days blocks the 3-day follow-up"
    assert any(q["status"] == "queued" and q["status_reason"] == "deferred:frequency_cap" for q in step2)
