"""Deterministic, explainable ICP scoring. No LLM is involved in any number produced here.

total_score_0_100 = sum over criteria of  weight_i / sum(weights) * score_i * 100
Missing data scores 0 for that criterion and is flagged (missing=True), so unknowns never inflate a score.
Hard disqualifiers are evaluated separately and force tier "disqualified" regardless of the weighted score.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

CRITERIA = [
    "industry",
    "company_scale",
    "geography",
    "business_model",
    "buyer_role_fit",
    "observed_signals",
    "source_confidence",
    "data_freshness",
]

ROLE_FIT = {"economic_buyer": 1.0, "champion": 0.8, "technical": 0.6, "user": 0.4, "unknown": 0.1}


@dataclass
class Criterion:
    name: str
    weight: float
    raw_value: object
    score_0_1: float
    contribution: float
    evidence_ids: list[str] = field(default_factory=list)
    missing: bool = False
    note: str = ""


@dataclass
class ScoreResult:
    model_version: str
    hard_disqualifiers: list[dict]
    criteria: list[Criterion]
    total_score_0_100: float
    tier: str
    missing_data: list[str]


def model_version(icp_version: int, definition: dict, weights: dict, disqualifiers: list, thresholds: dict) -> str:
    h = hashlib.sha256(
        json.dumps([definition, weights, disqualifiers, thresholds], sort_keys=True).encode()
    ).hexdigest()[:8]
    return f"icp-v{icp_version}-{h}"


def _score_criterion(name: str, facts: dict, d: dict, now: datetime) -> tuple[object, float | None, str]:
    """Returns (raw_value, score or None when missing, note)."""
    if name == "industry":
        v = facts.get("industry")
        if not v:
            return None, None, "industry unknown"
        if v in d.get("industries", []):
            return v, 1.0, "target industry"
        if v in d.get("adjacent_industries", []):
            return v, 0.5, "adjacent industry"
        return v, 0.0, "not a target industry"
    if name == "company_scale":
        v = facts.get("employee_band")
        if not v:
            return None, None, "employee band unknown"
        if v in d.get("employee_bands", []):
            return v, 1.0, "target size band"
        if v in d.get("adjacent_employee_bands", []):
            return v, 0.5, "adjacent size band"
        return v, 0.0, "outside size bands"
    if name == "geography":
        c, r = facts.get("country"), facts.get("region")
        if not c and not r:
            return None, None, "location unknown"
        if c and c in d.get("countries", []):
            return c, 1.0, "target country"
        if r and r in d.get("regions", []):
            return c or r, 0.6, "target region, non-target country"
        return c or r, 0.0, "outside target geography"
    if name == "business_model":
        v = facts.get("business_model")
        if not v:
            return None, None, "business model unknown"
        return v, (1.0 if v in d.get("business_models", []) else 0.0), ""
    if name == "buyer_role_fit":
        roles = facts.get("buyer_roles")
        if not roles:
            return None, None, "no matched contacts yet"
        targets = set(d.get("buyer_roles", []))
        best = max((ROLE_FIT.get(r, 0.1) if (not targets or r in targets or r == "user") else 0.1) for r in roles)
        return sorted(set(roles)), best, "best matched buyer role"
    if name == "observed_signals":
        signals = facts.get("signals")
        if signals is None:
            return None, None, "no signal research yet"
        wanted = set(d.get("signals", []))
        matched = sorted({s for s in signals if s in wanted})
        return matched, min(1.0, len(matched) / 2), f"{len(matched)} target signal(s); 2+ scores full marks"
    if name == "source_confidence":
        v = facts.get("source_confidence")
        if v is None:
            return None, None, "no provenance"
        return round(float(v), 2), max(0.0, min(1.0, float(v))), "highest source confidence"
    if name == "data_freshness":
        v = facts.get("last_verified_at")
        if not v:
            return None, None, "never verified"
        if isinstance(v, str):
            v = datetime.fromisoformat(v)
        if v.tzinfo is None:
            v = v.replace(tzinfo=UTC)
        days = max(0, (now - v).days)
        f = d.get("freshness_days", {"fresh": 30, "ok": 90, "stale": 180})
        s = 1.0 if days <= f["fresh"] else 0.7 if days <= f["ok"] else 0.4 if days <= f["stale"] else 0.1
        return f"{days}d", s, "days since last verification"
    raise KeyError(name)


def evaluate_disqualifiers(facts: dict, rules: list[dict]) -> list[dict]:
    hits = []
    for r in rules:
        rule, values = r.get("rule"), r.get("values", [])
        hit = False
        if rule == "industry_in":
            hit = facts.get("industry") in values
        elif rule == "country_not_in":
            hit = bool(facts.get("country")) and facts.get("country") not in values
        elif rule == "employee_band_in":
            hit = facts.get("employee_band") in values
        elif rule == "domain_suppressed":
            hit = bool(facts.get("domain_suppressed"))
        elif rule == "is_competitor":
            hit = bool(facts.get("is_competitor"))
        elif rule == "contact_suppressed":
            hit = bool(facts.get("contact_suppressed"))
        elif rule == "email_invalid":
            hit = facts.get("email_status") in ("syntax_invalid", "bounced")
        if hit:
            hits.append({"rule": rule, "reason": r.get("reason", rule)})
    return hits


def score(
    facts: dict,
    *,
    icp_version: int,
    definition: dict,
    weights: dict,
    disqualifiers: list[dict],
    thresholds: dict,
    evidence: dict[str, list[str]] | None = None,
    now: datetime | None = None,
) -> ScoreResult:
    now = now or datetime.now(UTC)
    evidence = evidence or {}
    total_w = sum(float(weights.get(c, 0)) for c in CRITERIA) or 1.0
    crits: list[Criterion] = []
    missing: list[str] = []
    for c in CRITERIA:
        w = float(weights.get(c, 0))
        raw, s, note = _score_criterion(c, facts, definition, now)
        is_missing = s is None
        if is_missing:
            missing.append(c)
        sc = 0.0 if s is None else s
        crits.append(
            Criterion(c, w, raw, round(sc, 3), round(w / total_w * sc * 100, 2), evidence.get(c, []), is_missing, note)
        )
    total = round(sum(c.contribution for c in crits), 1)
    dq = evaluate_disqualifiers(facts, disqualifiers)
    if dq:
        tier = "disqualified"
    elif total >= thresholds.get("A", 75):
        tier = "A"
    elif total >= thresholds.get("B", 55):
        tier = "B"
    else:
        tier = "C"
    return ScoreResult(
        model_version(icp_version, definition, weights, disqualifiers, thresholds), dq, crits, total, tier, missing
    )
