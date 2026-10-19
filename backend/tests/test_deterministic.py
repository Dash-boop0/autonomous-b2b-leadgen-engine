"""Unit tests for deterministic code: scoring, verification levels, metrics, normalisation."""

from datetime import UTC, datetime, timedelta

import pytest

from app import metrics, normalise, scoring, verification

DEF = {
    "industries": ["Logistics"],
    "adjacent_industries": ["Retail"],
    "employee_bands": ["51-200"],
    "adjacent_employee_bands": ["11-50"],
    "countries": ["GB"],
    "regions": ["Europe"],
    "business_models": ["B2B"],
    "buyer_roles": ["economic_buyer", "champion"],
    "signals": ["hiring_operations", "erp_migration"],
    "freshness_days": {"fresh": 30, "ok": 90, "stale": 180},
}
W = {c: 10 for c in scoring.CRITERIA}
NOW = datetime(2026, 10, 1, tzinfo=UTC)
FULL = {
    "industry": "Logistics",
    "employee_band": "51-200",
    "country": "GB",
    "region": "Europe",
    "business_model": "B2B",
    "buyer_roles": ["economic_buyer"],
    "signals": ["hiring_operations", "erp_migration"],
    "source_confidence": 1.0,
    "last_verified_at": NOW - timedelta(days=5),
}


def _score(facts, disq=None, weights=W):
    return scoring.score(
        facts,
        icp_version=1,
        definition=DEF,
        weights=weights,
        disqualifiers=disq or [],
        thresholds={"A": 80, "B": 60},
        now=NOW,
    )


def test_perfect_fit_scores_100_and_contributions_sum():
    r = _score(FULL)
    assert r.total_score_0_100 == 100.0 and r.tier == "A"
    assert sum(c.contribution for c in r.criteria) == pytest.approx(100.0)


def test_missing_data_scores_zero_and_is_flagged():
    facts = {**FULL, "signals": None, "buyer_roles": None}
    r = _score(facts)
    assert set(r.missing_data) == {"observed_signals", "buyer_role_fit"}
    assert r.total_score_0_100 == 75.0
    assert all(c.contribution == 0 for c in r.criteria if c.missing)


def test_partial_matches_and_weights():
    facts = {**FULL, "industry": "Retail", "employee_band": "11-50", "country": "DE"}
    r = _score(facts)
    by = {c.name: c for c in r.criteria}
    assert by["industry"].score_0_1 == 0.5 and by["company_scale"].score_0_1 == 0.5
    assert by["geography"].score_0_1 == 0.6
    w = {**W, "industry": 0}
    assert {c.name: c.contribution for c in _score(facts, weights=w).criteria}["industry"] == 0


def test_hard_disqualifier_overrides_weighted_score():
    r = _score({**FULL, "industry": "Gambling"}, [{"rule": "industry_in", "values": ["Gambling"], "reason": "policy"}])
    assert r.tier == "disqualified" and r.hard_disqualifiers == [{"rule": "industry_in", "reason": "policy"}]
    assert r.total_score_0_100 > 0, "weighted score still reported for transparency"


def test_country_disqualifier_ignores_missing_country():
    rule = [{"rule": "country_not_in", "values": ["GB"], "reason": "outside"}]
    assert _score({**FULL, "country": None, "region": None}, rule).tier != "disqualified"
    assert _score({**FULL, "country": "US"}, rule).tier == "disqualified"


def test_freshness_bands():
    by = lambda d: {c.name: c for c in _score({**FULL, "last_verified_at": NOW - timedelta(days=d)}).criteria}  # noqa: E731
    assert by(10)["data_freshness"].score_0_1 == 1.0
    assert by(60)["data_freshness"].score_0_1 == 0.7
    assert by(400)["data_freshness"].score_0_1 == 0.1


def test_model_version_changes_with_weights():
    v1 = scoring.model_version(1, DEF, W, [], {"A": 80})
    assert v1 == scoring.model_version(1, DEF, dict(W), [], {"A": 80})
    assert v1 != scoring.model_version(1, DEF, {**W, "industry": 11}, [], {"A": 80})


@pytest.mark.parametrize(
    "email,ok",
    [
        ("a.b@example.com", True),
        ("a@@example.com", False),
        ("a..b@example.com", False),
        ("noat.example.com", False),
        (".a@example.com", False),
        ("a@example", False),
        ("", False),
        (None, False),
        ("o'neil@example.org", True),
    ],
)
def test_syntax(email, ok):
    assert verification.syntax_ok(email) is ok


class _DNS:
    name, mock = "fake-dns", True

    def __init__(self, mx):
        self.mx = mx

    def has_mx(self, d):
        return self.mx


class _Ver:
    name, mock = "fake-verifier", True

    def __init__(self, r):
        self.r = r

    def verify(self, e):
        return self.r


def test_verification_levels_are_distinct_and_honest():
    assert verification.verify_email("bad@@x.com", _DNS(True), _Ver("valid")).status == "syntax_invalid"
    assert verification.verify_email("a@x.com", _DNS(False), _Ver("valid")).status == "syntax_valid"
    assert verification.verify_email("a@x.com", _DNS(None), _Ver("valid")).checks[-1]["result"] == "error"
    assert verification.verify_email("a@x.com", _DNS(True), _Ver("unknown")).status == "domain_has_mx"
    risky = verification.verify_email("a@x.com", _DNS(True), _Ver("invalid"))
    assert risky.status == "domain_has_mx" and risky.risky
    ok = verification.verify_email("a@x.com", _DNS(True), _Ver("valid"))
    assert ok.status == "provider_verified" and [c["check"] for c in ok.checks] == ["syntax", "domain_mx", "provider"]
    assert all(c["mock"] for c in ok.checks[1:])
    for r in ("valid", "invalid", "catch_all", "unknown"):
        assert verification.verify_email("a@x.com", _DNS(True), _Ver(r)).status != "deliverable_confirmed"


def test_only_events_change_deliverability():
    assert verification.status_after_event("provider_verified", "delivered") == "deliverable_confirmed"
    assert verification.status_after_event("deliverable_confirmed", "bounced") == "bounced"
    assert verification.status_after_event("bounced", "delivered") == "bounced"
    assert verification.status_after_event("domain_has_mx", "replied") == "domain_has_mx"


def test_metrics_formulas_contact_level():
    sends = [
        {"contact_id": c, "step_no": s, "status": st}
        for c, s, st in [
            ("a", 1, "sent_simulated"),
            ("a", 2, "sent_simulated"),
            ("b", 1, "sent_simulated"),
            ("c", 1, "sent_simulated"),
            ("d", 1, "sent_simulated"),
            ("e", 1, "blocked"),
            ("b", 2, "cancelled"),
        ]
    ]
    events = [
        {"contact_id": c, "event_type": t}
        for c, t in [
            ("a", "delivered"),
            ("a", "delivered"),
            ("b", "delivered"),
            ("c", "bounced"),
            ("d", "delivered"),
            ("e", "delivered"),
        ]
    ]
    replies = [
        {"contact_id": "a", "classification": "interested"},
        {"contact_id": "b", "classification": "out_of_office"},
        {"contact_id": "d", "classification": "unsubscribe"},
    ]
    m = metrics.campaign_metrics(sends, events, replies)
    c, r = m["counts"], m["rates"]
    assert c["messages_sent"] == 5 and c["contacts_sent"] == 4
    assert r["delivery_rate"]["numerator"] == 3 and r["delivery_rate"]["denominator"] == 4
    assert r["bounce_rate"]["value"] == 0.25
    assert r["reply_rate"]["numerator"] == 1 and r["reply_rate"]["denominator"] == 3, "OOO and unsubscribe excluded"
    assert r["positive_reply_rate"]["value"] == 1.0
    assert r["unsubscribe_rate"]["numerator"] == 1
    assert c["followups_cancelled"] == 1 and c["blocked_sends"] == 1


def test_zero_denominator_is_none_not_zero():
    m = metrics.campaign_metrics([], [], [])
    assert all(v["value"] is None and v["denominator"] == 0 for v in m["rates"].values())


def test_segment_breakdown_partitions_contacts():
    sends = [{"contact_id": c, "step_no": 1, "status": "sent_simulated"} for c in "abc"]
    events = [{"contact_id": c, "event_type": "delivered"} for c in "abc"]
    seg = metrics.segment_breakdown(sends, events, [], key=lambda c: "x" if c == "a" else "y")
    assert {s["segment"]: s["counts"]["contacts_sent"] for s in seg} == {"x": 1, "y": 2}


def test_normalisation_and_dedup_keys():
    assert normalise.normalise_domain("https://WWW.Brightlane.example.com/about?x=1") == "brightlane.example.com"
    assert normalise.normalise_company_name("Copperfield Wholesale Ltd.") == "copperfield wholesale"
    assert normalise.normalise_company_name("Kestrel Components GmbH") == "kestrel components"
    assert normalise.normalise_country("United Kingdom") == "GB" and normalise.normalise_country("ie") == "IE"
    assert normalise.employee_band("180") == "51-200" and normalise.employee_band("201-500") == "201-500"
    assert normalise.employee_band(8) == "1-10" and normalise.employee_band("abc") is None
    assert normalise.account_dedup_key("X Ltd", "www.x.example.com", "GB") == "domain:x.example.com"
    assert normalise.account_dedup_key("Umberfield Textiles", None, "GB") == "name:umberfield textiles|GB"
    assert normalise.contact_dedup_key("acc", "A B", "A@X.com") == "email:a@x.com"
