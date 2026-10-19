"""Full API-level end-to-end run of the simulated pipeline (all synthetic, dry-run, mock providers)."""

from tests.conftest import build_pipeline


def test_full_pipeline_end_to_end(api):
    health = api.ok(api.c.get("/health"))
    assert health["status"] == "ok" and health["db"]["seeded"] is True

    ids = build_pipeline(api)
    cid = ids["campaign_id"]

    detail = api.ok(api.get(f"/api/campaigns/{cid}"))
    assert detail["drafts"], "personalisation produced drafts"
    assert all(d["policy_passed"] for d in detail["drafts"]), "seed drafts pass policy checks"
    assert not detail["queue"], "nothing is queued before human approval"

    res = api.approve("campaign.activate")
    assert res["resumed"]["status"] == "completed"
    detail = api.ok(api.get(f"/api/campaigns/{cid}"))
    assert detail["campaign"]["status"] == "active"
    assert {q["status"] for q in detail["queue"]} <= {"queued", "blocked"}

    cyc = api.ok(api.post(f"/api/campaigns/{cid}/conversation-cycle"))
    assert cyc["run"]["status"] == "completed", cyc
    queue = api.ok(api.get(f"/api/campaigns/{cid}"))["queue"]
    assert any(q["status"] == "sent_simulated" and q["mode"] == "dry_run" for q in queue)
    assert any(q["status"] == "cancelled" and q["step_no"] == 2 for q in queue), "follow-ups cancelled after replies"

    replies = api.ok(api.get("/api/replies"))
    labels = {r["classification"] for r in replies}
    assert {"interested", "unsubscribe", "question_pricing", "question_product", "out_of_office"} <= labels
    pricing = next(r for r in replies if r["classification"] == "question_pricing")
    assert pricing["response_status"] == "escalated"
    product = next(r for r in replies if r["classification"] == "question_product")
    assert product["response_status"] == "drafted" and product["response_citations"]

    api.ok(api.post("/api/system/sim-clock", json={"days": 3}))
    cyc2 = api.ok(api.post(f"/api/campaigns/{cid}/conversation-cycle"))
    assert cyc2["run"]["status"] == "completed"
    queue = api.ok(api.get(f"/api/campaigns/{cid}"))["queue"]
    assert any(q["status"] == "sent_simulated" and q["step_no"] == 2 for q in queue), "follow-ups to non-responders"

    analytics = api.ok(api.get(f"/api/analytics/campaigns/{cid}"))
    assert analytics["simulated"] is True
    assert analytics["rates"]["reply_rate"]["denominator"] == analytics["counts"]["delivered_contacts"]

    imp = api.ok(api.post(f"/api/analytics/campaigns/{cid}/improve", json={}))
    assert imp["run"]["status"] == "waiting_approval"
    done = api.approve("icp.update")
    assert done["resumed"]["status"] == "completed"
    versions = api.ok(api.get("/api/icp"))
    assert [v["status"] for v in versions[:2]] == ["approved", "superseded"]

    crm = api.ok(api.get("/api/crm/outbox"))
    assert crm, "mock CRM received lead stage updates"
    audit = api.ok(api.get("/api/audit?limit=500"))
    actions = {e["action"] for e in audit["items"]}
    assert {"approval.approved", "send.dispatched_dry_run", "workflow.completed"} <= actions
    export = api.ok(api.get("/api/export/shared"))
    assert export["synthetic"] and export["accounts"] and export["contacts"]
