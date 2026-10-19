"""Market & strategy agents: Market Research, Buyer Persona, Competitor & Market Gap, ICP Builder."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..models import ICPVersion, Offer, ResearchArtifact
from ..scoring import model_version
from .base import AgentOutput, BaseAgent

MARKET_NOTES_REF = "fixture:market/segments.md (synthetic)"
COMPETITOR_NOTES_REF = "fixture:market/competitors.md (synthetic)"


# ---------------------------------------------------------------- Market Research


class MarketResearchInput(BaseModel):
    offer_id: str


class Segment(BaseModel):
    name: str
    industry: str
    fit_score: float = Field(ge=0, le=1)
    account_count: int
    keyword_hits: list[str]
    summary: str
    evidence_ids: list[str]


class MarketResearchOutput(AgentOutput):
    segments: list[Segment] = Field(default_factory=list)


class MarketResearchAgent(BaseAgent):
    name = "market_research"
    group = "Market & strategy"
    description = "Ranks market segments for an offer using permitted notes and registry counts; cites every claim."
    tools = ("fixtures.market_notes", "registry.search", "llm.generate")
    input_model = MarketResearchInput
    output_model = MarketResearchOutput
    confidence_threshold = 0.5
    completion_criteria = "At least one segment with evidence and a registry account count."

    def run(self, inp: MarketResearchInput) -> MarketResearchOutput:
        offer = self.session.get(Offer, inp.offer_id)
        if offer is None:
            raise ValueError("offer not found")
        notes = self.tool("fixtures.market_notes")
        kw = [k.lower() for k in offer.keywords]
        src = self.source_record(
            "market_notes",
            "synthetic",
            MARKET_NOTES_REF,
            {"sections": sorted(notes)},
            0.5,
            "Synthetic notes written for the demo",
        )
        segments = []
        for industry, text in notes.items():
            hits = sorted({k for k in kw if k in text.lower()})
            recs = self.tool("registry.search", industries=[industry], countries=offer.target_countries)
            # deterministic fit: keyword coverage (60%) + presence in permitted sources (40%, saturating at 3)
            fit = round(0.6 * (len(hits) / max(1, len(kw))) + 0.4 * min(1.0, len(recs) / 3), 3)
            first_sentence = re.split(r"(?<=\.)\s", text, maxsplit=1)[0]
            ev = self.add_evidence(
                "offer", offer.id, f"Market notes on {industry}", first_sentence, src.id, 0.5, kind="research"
            )
            ev2 = self.add_evidence(
                "offer",
                offer.id,
                f"{len(recs)} {industry} account(s) in mock registry for {', '.join(offer.target_countries)}",
                "",
                None,
                0.8,
                kind="research",
            )
            summary = self.llm(
                "summarise_segment", {"segment": industry, "count": len(recs), "notes": [first_sentence]}
            )
            segments.append(
                Segment(
                    name=f"{industry} ({', '.join(offer.target_countries)})",
                    industry=industry,
                    fit_score=fit,
                    account_count=len(recs),
                    keyword_hits=hits,
                    summary=summary.text,
                    evidence_ids=[ev, ev2],
                )
            )
        segments.sort(key=lambda s: -s.fit_score)
        for s in segments:
            self.session.add(
                ResearchArtifact(
                    run_id=self.ctx.run_id,
                    offer_id=offer.id,
                    kind="segment",
                    name=s.name,
                    data=s.model_dump(),
                    evidence_ids=s.evidence_ids,
                    agent=self.name,
                    confidence=0.5 + 0.5 * s.fit_score,
                )
            )
        conf = round(sum(s.fit_score for s in segments[:3]) / max(1, min(3, len(segments))), 2) if segments else 0.0
        return MarketResearchOutput(
            segments=segments,
            confidence=max(conf, 0.5 if segments else 0.0),
            notes=["Notes are synthetic; counts come from the mock registry fixture."],
        )

    def check_completion(self, out: MarketResearchOutput) -> list[str]:
        return [] if any(s.evidence_ids for s in out.segments) else ["no evidenced segment"]


# ---------------------------------------------------------------- Buyer Persona

PERSONA_LIBRARY = [
    {
        "name": "Operations leader",
        "buyer_role": "economic_buyer",
        "seniority": "executive",
        "title_keywords": [
            "chief operating officer",
            "coo",
            "operations director",
            "vp operations",
            "managing director",
            "head of supply chain",
            "finance director",
            "cfo",
        ],
        "pains": ["missed service levels", "working capital tied in stock", "planning depends on one analyst"],
    },
    {
        "name": "Planning owner",
        "buyer_role": "champion",
        "seniority": "manager",
        "title_keywords": ["demand planning", "supply chain manager", "inventory manager", "head of planning"],
        "pains": ["manual spreadsheet forecasts", "stockouts on fast movers", "no forecast audit trail"],
    },
    {
        "name": "Data & systems owner",
        "buyer_role": "technical",
        "seniority": "manager",
        "title_keywords": ["head of it", "it director", "bi lead", "erp manager", "data engineer"],
        "pains": ["integration effort", "data security review", "ERP migration workload"],
    },
    {
        "name": "Planner / analyst",
        "buyer_role": "user",
        "seniority": "individual",
        "title_keywords": ["planner", "analyst", "transport planner"],
        "pains": ["repetitive data prep", "late customer forecasts"],
    },
]


class PersonaInput(BaseModel):
    offer_id: str
    industries: list[str]


class Persona(BaseModel):
    name: str
    buyer_role: str
    seniority: str
    title_keywords: list[str]
    pains: list[str]
    summary: str


class PersonaOutput(AgentOutput):
    personas: list[Persona] = Field(default_factory=list)


class BuyerPersonaAgent(BaseAgent):
    name = "buyer_persona"
    group = "Market & strategy"
    description = "Maps buyer roles (economic buyer, champion, technical, user) to title keywords and pains."
    tools = ("llm.generate",)
    input_model = PersonaInput
    output_model = PersonaOutput
    confidence_threshold = 0.6
    completion_criteria = "Personas cover at least the economic buyer and champion roles."

    def run(self, inp: PersonaInput) -> PersonaOutput:
        personas = []
        for p in PERSONA_LIBRARY:
            summary = self.llm("persona_summary", p).text
            personas.append(Persona(**p, summary=summary))
            self.session.add(
                ResearchArtifact(
                    run_id=self.ctx.run_id,
                    offer_id=inp.offer_id,
                    kind="persona",
                    name=p["name"],
                    data={**p, "summary": summary},
                    agent=self.name,
                    confidence=0.65,
                )
            )
        return PersonaOutput(
            personas=personas,
            confidence=0.65,
            notes=["Template personas for review; not derived from customer interviews."],
        )

    def check_completion(self, out: PersonaOutput) -> list[str]:
        roles = {p.buyer_role for p in out.personas}
        return [f"missing {r}" for r in ("economic_buyer", "champion") if r not in roles]


# ---------------------------------------------------------------- Competitor & Market Gap


class CompetitorInput(BaseModel):
    offer_id: str


class CompetitorGap(BaseModel):
    competitor: str
    gap: str
    evidence_id: str


class CompetitorOutput(AgentOutput):
    gaps: list[CompetitorGap] = Field(default_factory=list)
    competitor_names: list[str] = Field(default_factory=list)


class CompetitorGapAgent(BaseAgent):
    name = "competitor_gap"
    group = "Market & strategy"
    description = "Extracts competitor positioning and market gaps from permitted notes, with citations."
    tools = ("fixtures.competitor_notes", "llm.generate")
    input_model = CompetitorInput
    output_model = CompetitorOutput
    confidence_threshold = 0.5
    completion_criteria = "Every gap cites a source excerpt."

    def run(self, inp: CompetitorInput) -> CompetitorOutput:
        notes = self.tool("fixtures.competitor_notes")
        src = self.source_record(
            "competitor_notes",
            "synthetic",
            COMPETITOR_NOTES_REF,
            {"sections": sorted(notes)},
            0.5,
            "Synthetic notes about fictional vendors",
        )
        gaps, names = [], []
        for comp, text in notes.items():
            m = re.search(r"Gap:\s*(.+?)(?:$|\n)", text)
            gap = m.group(1).strip() if m else "no gap stated in notes"
            ev = self.add_evidence(
                "offer", inp.offer_id, f"Competitor gap: {comp}", text[:300], src.id, 0.5, kind="research"
            )
            gaps.append(
                CompetitorGap(
                    competitor=comp,
                    gap=self.llm("competitor_summary", {"competitor": comp, "gap": gap}).text,
                    evidence_id=ev,
                )
            )
            if "(fictional)" in comp:
                names.append(comp.replace("(fictional)", "").strip())
            self.session.add(
                ResearchArtifact(
                    run_id=self.ctx.run_id,
                    offer_id=inp.offer_id,
                    kind="competitor_gap",
                    name=comp,
                    data={"gap": gap},
                    evidence_ids=[ev],
                    agent=self.name,
                    confidence=0.5,
                )
            )
        return CompetitorOutput(gaps=gaps, competitor_names=names, confidence=0.55)

    def check_completion(self, out: CompetitorOutput) -> list[str]:
        return [] if all(g.evidence_id for g in out.gaps) else ["gap without evidence"]


# ---------------------------------------------------------------- ICP Builder

DEFAULT_WEIGHTS = {
    "industry": 20,
    "company_scale": 15,
    "geography": 15,
    "business_model": 10,
    "buyer_role_fit": 15,
    "observed_signals": 15,
    "source_confidence": 5,
    "data_freshness": 5,
}
SERVED_COUNTRIES = ["GB", "IE", "NL", "BE", "DE", "FR", "DK", "ES"]


class ICPBuildInput(BaseModel):
    offer_id: str
    segments: list[Segment]
    personas: list[Persona]
    competitor_names: list[str] = Field(default_factory=list)
    parent_version_id: str | None = None


class ICPBuildOutput(AgentOutput):
    icp_version_id: str
    version: int
    model_version: str


class ICPBuilderAgent(BaseAgent):
    name = "icp_builder"
    group = "Market & strategy"
    description = (
        "Turns research into a versioned ICP + scoring model draft. It cannot activate it: a human must approve."
    )
    tools = ("llm.generate",)
    input_model = ICPBuildInput
    output_model = ICPBuildOutput
    confidence_threshold = 0.5
    completion_criteria = "A draft ICP version exists with weights summing to > 0 and at least one hard disqualifier."

    def run(self, inp: ICPBuildInput) -> ICPBuildOutput:
        offer = self.session.get(Offer, inp.offer_id)
        assert offer is not None
        ranked = sorted(inp.segments, key=lambda s: -s.fit_score)
        industries = [s.industry for s in ranked if s.fit_score >= 0.5][:3] or [ranked[0].industry]
        adjacent = [s.industry for s in ranked if s.industry not in industries][:2]
        roles = ["economic_buyer", "champion", "technical"]
        definition = {
            "industries": industries,
            "adjacent_industries": adjacent,
            "employee_bands": ["51-200", "201-500", "501-1000"],
            "adjacent_employee_bands": ["11-50", "1001-5000"],
            "countries": offer.target_countries,
            "regions": ["Europe"],
            "business_models": ["B2B"],
            "buyer_roles": roles,
            "personas": [p.model_dump(include={"name", "buyer_role", "title_keywords"}) for p in inp.personas],
            "signals": ["hiring_operations", "warehouse_expansion", "erp_migration", "forecasting_pain"],
            "competitors": inp.competitor_names,
            "freshness_days": {"fresh": 30, "ok": 90, "stale": 180},
        }
        disq = [
            {
                "rule": "industry_in",
                "values": ["Gambling", "Tobacco", "Weapons"],
                "reason": "Excluded industry (policy)",
            },
            {"rule": "country_not_in", "values": SERVED_COUNTRIES, "reason": "Outside countries we can serve"},
            {"rule": "employee_band_in", "values": ["1-10"], "reason": "Below minimum company size"},
            {"rule": "is_competitor", "reason": "Known competitor"},
            {"rule": "domain_suppressed", "reason": "Domain is on the suppression list"},
        ]
        thresholds = {"A": 80, "B": 60}
        version = (self.session.scalar(select(func.max(ICPVersion.version))) or 0) + 1
        rationale = self.llm(
            "icp_rationale", {"industries": industries, "countries": offer.target_countries, "roles": roles}
        ).text
        icp = ICPVersion(
            version=version,
            status="pending_approval",
            offer_id=offer.id,
            name=f"ICP v{version} - {offer.name}",
            definition=definition,
            weights=dict(DEFAULT_WEIGHTS),
            hard_disqualifiers=disq,
            tier_thresholds=thresholds,
            rationale=rationale,
            created_by=f"agent:{self.name}",
            parent_version_id=inp.parent_version_id,
            change_summary="Initial draft from market research" if not inp.parent_version_id else "",
        )
        self.session.add(icp)
        self.session.flush()
        evidence = [e for s in ranked[:3] for e in s.evidence_ids]
        return ICPBuildOutput(
            icp_version_id=icp.id,
            version=version,
            model_version=model_version(version, definition, icp.weights, disq, thresholds),
            confidence=0.6,
            evidence_ids=evidence,
        )

    def check_completion(self, out: ICPBuildOutput) -> list[str]:
        icp = self.session.get(ICPVersion, out.icp_version_id)
        if icp is None:
            return ["ICP not persisted"]
        unmet = []
        if sum(icp.weights.values()) <= 0:
            unmet.append("weights sum to 0")
        if not icp.hard_disqualifiers:
            unmet.append("no hard disqualifiers")
        return unmet
