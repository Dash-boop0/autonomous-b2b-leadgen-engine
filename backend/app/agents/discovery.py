"""Discovery & data agents: Account Discovery, Data Enrichment, Public Web Research, Buying Signal, Contact Discovery,
Contact Verification and Lead Scoring. Deterministic helpers (dedup, scoring, verification) live outside the agents."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from ..adapters.sources import AdapterError
from ..models import (
    Account,
    Contact,
    Evidence,
    ICPVersion,
    Lead,
    QualificationResult,
    SourceRecord,
    utcnow,
)
from ..normalise import (
    COUNTRY_REGION,
    account_dedup_key,
    contact_dedup_key,
    employee_band,
    normalise_country,
    normalise_domain,
    normalise_email,
)
from .base import AgentOutput, BaseAgent, TransientAgentError

# ---------------------------------------------------------------- Account Discovery


class Candidate(BaseModel):
    source_id: str
    adapter: str
    fields: dict


class Rejected(BaseModel):
    ref: str
    reason: str


class AccountDiscoveryInput(BaseModel):
    icp_version_id: str
    adapters: list[str] = Field(default_factory=lambda: ["mock_registry"])
    csv_upload_id: str | None = None


class AccountDiscoveryOutput(AgentOutput):
    candidates: list[Candidate] = Field(default_factory=list)
    rejected: list[Rejected] = Field(default_factory=list)
    per_adapter: dict[str, int] = Field(default_factory=dict)


class AccountDiscoveryAgent(BaseAgent):
    name = "account_discovery"
    group = "Discovery & data"
    description = (
        "Pulls candidate accounts from permitted adapters only (CSV upload, mock registry API) with provenance."
    )
    tools = ("csv.read_upload", "registry.search", "sources.adapter_config")
    input_model = AccountDiscoveryInput
    output_model = AccountDiscoveryOutput
    completion_criteria = "Every candidate has a SourceRecord; every skipped row has a reason."

    def run(self, inp: AccountDiscoveryInput) -> AccountDiscoveryOutput:
        icp = self.session.get(ICPVersion, inp.icp_version_id)
        if icp is None or icp.status != "approved":
            raise PermissionError("Account discovery requires an approved ICP version")
        out = AccountDiscoveryOutput()
        d = icp.definition
        for adapter_id in inp.adapters:
            if adapter_id == "csv_upload":
                if not inp.csv_upload_id:
                    out.notes.append("csv_upload selected but no upload given")
                    continue
                adapter = self.tool("sources.adapter_config", adapter_id="csv_upload")
                upload, rows = self.tool("csv.read_upload", upload_id=inp.csv_upload_id)
                from ..adapters.sources import domain_allowed

                for r in rows:
                    ref = f"csv:{upload.filename}#row{r['_row']}"
                    if not r.get("company_name"):
                        out.rejected.append(Rejected(ref=ref, reason="missing company_name"))
                        continue
                    ok, why = domain_allowed(adapter, r.get("website"))
                    if r.get("website") and not ok:
                        out.rejected.append(Rejected(ref=ref, reason=f"domain {why}"))
                        continue
                    payload = {k: v for k, v in r.items() if k != "_row"}
                    src = self.source_record(
                        "csv_upload", "csv", ref, payload, 0.7, adapter.terms_note, upload.uploaded_at
                    )
                    out.candidates.append(
                        Candidate(
                            source_id=src.id,
                            adapter="csv_upload",
                            fields={
                                "name": r["company_name"],
                                "domain": r.get("website"),
                                "industry": r.get("industry"),
                                "employees": r.get("employees"),
                                "country": r.get("country"),
                                "business_model": r.get("business_model"),
                                "revenue_band": r.get("revenue_band"),
                            },
                        )
                    )
                upload.consumed_at = utcnow()
            elif adapter_id == "mock_registry":
                from ..adapters.sources import TransientAdapterError

                try:
                    recs = self.tool(
                        "registry.search",
                        industries=d["industries"] + d.get("adjacent_industries", []),
                        countries=[c for c in d["countries"]] + ["BE", "DE"],
                    )
                except TransientAdapterError as e:
                    raise TransientAgentError(str(e)) from e
                for rec in recs:
                    c = rec.payload
                    src = self.source_record(
                        "mock_registry", "registry", rec.url_or_ref, c, rec.confidence, rec.terms_note
                    )
                    out.candidates.append(
                        Candidate(
                            source_id=src.id,
                            adapter="mock_registry",
                            fields={
                                "name": c["name"],
                                "domain": c["domain"],
                                "industry": c["industry"],
                                "employees": c["employee_count"],
                                "country": c["country"],
                                "business_model": c["business_model"],
                                "revenue_band": c["revenue_band"],
                                "last_filed": c.get("last_filed"),
                            },
                        )
                    )
            else:
                raise AdapterError(f"Adapter {adapter_id} cannot discover accounts")
            out.per_adapter[adapter_id] = sum(1 for c in out.candidates if c.adapter == adapter_id)
        out.confidence = 0.8 if out.candidates else 0.0
        return out

    def check_completion(self, out: AccountDiscoveryOutput) -> list[str]:
        return [] if all(c.source_id for c in out.candidates) else ["candidate without provenance"]


# ---------------------------------------------------------------- Data Enrichment (normalisation)

INDUSTRY_ALIASES = {
    "wholesale distribution": "Wholesale Distribution",
    "wholesale": "Wholesale Distribution",
    "logistics": "Logistics",
    "manufacturing": "Manufacturing",
    "retail": "Retail",
    "food & beverage": "Food & Beverage",
    "software": "Software",
    "gambling": "Gambling",
}

SENIORITY_RULES = [
    ("executive", r"\b(chief|coo|cfo|ceo|managing director|vp|vice president|director)\b"),
    ("head", r"\bhead of\b"),
    ("manager", r"\b(manager|lead)\b"),
    ("individual", r"."),
]
DEPARTMENT_RULES = [
    ("operations", r"operat|warehouse|transport|logistic"),
    ("supply_chain", r"supply|planning|planner|inventory|demand"),
    ("finance", r"financ|cfo"),
    ("it_data", r"\bit\b|data|bi lead|erp|engineer"),
    ("general_management", r"managing director|ceo"),
    ("marketing", r"marketing"),
]


class EnrichmentInput(BaseModel):
    mode: Literal["accounts", "contacts"]
    candidates: list[Candidate] = Field(default_factory=list)
    contact_ids: list[str] = Field(default_factory=list)


class NormalisedAccount(BaseModel):
    source_id: str
    adapter: str
    name: str
    domain: str | None
    industry: str | None
    employee_band: str | None
    revenue_band: str | None
    country: str | None
    region: str | None
    business_model: str | None
    dedup_key: str
    changes: list[str]


class EnrichmentOutput(AgentOutput):
    accounts: list[NormalisedAccount] = Field(default_factory=list)
    contacts_enriched: int = 0


class DataEnrichmentAgent(BaseAgent):
    name = "data_enrichment"
    group = "Discovery & data"
    description = (
        "Normalises firmographics (domain, country, size band, industry) and derives contact seniority/department."
    )
    tools = ()
    input_model = EnrichmentInput
    output_model = EnrichmentOutput
    completion_criteria = "Every account has a dedup key; every changed field is listed."

    def run(self, inp: EnrichmentInput) -> EnrichmentOutput:
        out = EnrichmentOutput(confidence=0.9)
        if inp.mode == "accounts":
            for c in inp.candidates:
                f, changes = c.fields, []
                dom = normalise_domain(f.get("domain"))
                if dom and dom != (f.get("domain") or ""):
                    changes.append(f"domain {f.get('domain')!r} -> {dom!r}")
                country = normalise_country(f.get("country"))
                if country and country != f.get("country"):
                    changes.append(f"country {f.get('country')!r} -> {country}")
                band = employee_band(f.get("employees"))
                ind_raw = (f.get("industry") or "").strip()
                industry = INDUSTRY_ALIASES.get(ind_raw.lower(), ind_raw or None)
                if industry != ind_raw and ind_raw:
                    changes.append(f"industry {ind_raw!r} -> {industry!r}")
                bm = (f.get("business_model") or "").upper() or None
                out.accounts.append(
                    NormalisedAccount(
                        source_id=c.source_id,
                        adapter=c.adapter,
                        name=f["name"].strip(),
                        domain=dom,
                        industry=industry,
                        employee_band=band,
                        revenue_band=f.get("revenue_band") or None,
                        country=country,
                        region=COUNTRY_REGION.get(country or ""),
                        business_model=bm,
                        dedup_key=account_dedup_key(f["name"], dom, country),
                        changes=changes,
                    )
                )
        else:
            for cid in inp.contact_ids:
                c = self.session.get(Contact, cid)
                if not c or not c.title:
                    continue
                t = c.title.lower()
                c.seniority = next(s for s, rx in SENIORITY_RULES if re.search(rx, t))
                c.department = next((d for d, rx in DEPARTMENT_RULES if re.search(rx, t)), "other")
                out.contacts_enriched += 1
        return out

    def check_completion(self, out: EnrichmentOutput) -> list[str]:
        return [] if all(a.dedup_key for a in out.accounts) else ["missing dedup key"]


def upsert_accounts(agent: BaseAgent, accounts: list[NormalisedAccount]) -> dict:
    """Deterministic de-duplication: merge by dedup key (normalised domain, else normalised name + country).
    Existing values win unless empty; every merge is recorded as evidence. Idempotent."""
    created = merged = 0
    ids: list[str] = []
    s = agent.session
    for a in accounts:
        acc = s.scalars(select(Account).where(Account.dedup_key == a.dedup_key)).first()
        src = s.get(SourceRecord, a.source_id)
        excerpt = f"industry={a.industry}; employees={a.employee_band}; country={a.country}; model={a.business_model}"
        if acc is None:
            acc = Account(
                name=a.name,
                domain=a.domain,
                industry=a.industry,
                employee_band=a.employee_band,
                revenue_band=a.revenue_band,
                country=a.country,
                region=a.region,
                business_model=a.business_model,
                source_ids=[a.source_id],
                dedup_key=a.dedup_key,
                last_verified_at=src.retrieved_at if src else utcnow(),
            )
            s.add(acc)
            s.flush()
            created += 1
        else:
            if a.source_id in acc.source_ids:
                ids.append(acc.id)
                continue  # same source record seen before: idempotent no-op
            filled = []
            for fld in ("domain", "industry", "employee_band", "revenue_band", "country", "region", "business_model"):
                if getattr(acc, fld) in (None, "") and getattr(a, fld):
                    setattr(acc, fld, getattr(a, fld))
                    filled.append(fld)
            acc.source_ids = [*acc.source_ids, a.source_id]
            if src and (acc.last_verified_at is None or src.retrieved_at > acc.last_verified_at):
                acc.last_verified_at = src.retrieved_at
            merged += 1
            agent.add_evidence(
                "account",
                acc.id,
                f"Merged duplicate record '{a.name}' from {a.adapter}",
                f"dedup key {a.dedup_key}; filled: {', '.join(filled) or 'nothing'}",
                a.source_id,
                0.9,
                kind="merge",
            )
        agent.add_evidence(
            "account",
            acc.id,
            f"Firmographics reported by {a.adapter}",
            excerpt,
            a.source_id,
            src.confidence if src else 0.5,
            kind="fact",
        )
        ids.append(acc.id)
    return {"created": created, "merged": merged, "account_ids": sorted(set(ids))}


# ---------------------------------------------------------------- Public Web Research

SIGNAL_PATTERNS = {
    "hiring_operations": r"\bhiring\b.*\b(operations|planner|warehouse|inventory|driver|analyst)",
    "warehouse_expansion": r"(new distribution centre|new warehouse|warehouse capacity|second plant|new cold store|expands)",
    "erp_migration": r"(migrating to a new erp|new erp)",
    "forecasting_pain": r"(stockouts?|forecast accuracy)",
}


class WebResearchInput(BaseModel):
    account_ids: list[str]


class PageFinding(BaseModel):
    account_id: str
    url: str
    status: str
    reason: str
    cached: bool
    signals: list[str]


class WebResearchOutput(AgentOutput):
    findings: list[PageFinding] = Field(default_factory=list)


class PublicWebResearchAgent(BaseAgent):
    name = "public_web_research"
    group = "Discovery & data"
    description = (
        "Reads permitted public pages (demo: local fixtures) honouring allowlist, robots rules, rate limits "
        "and cache; extracts signal sentences as evidence."
    )
    tools = ("web.fetch_public_page",)
    input_model = WebResearchInput
    output_model = WebResearchOutput
    completion_criteria = "Every account with a domain has a fetch outcome (ok, disallowed, blocked or not_found)."

    def run(self, inp: WebResearchInput) -> WebResearchOutput:
        out = WebResearchOutput(confidence=0.75)
        for aid in inp.account_ids:
            acc = self.session.get(Account, aid)
            if not acc or not acc.domain:
                continue
            res = self.tool("web.fetch_public_page", domain=acc.domain)
            found: list[str] = []
            if res["status"] == "ok":
                src = self.source_record(
                    "public_web_fixture",
                    "public_page",
                    res["url"],
                    {"url": res["url"], "content": res["content"]},
                    0.75,
                    "Fixture page; robots + allowlist honoured",
                )
                text = re.sub(r"<[^>]+>", "\n", res["content"])
                sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+|\n", text) if x.strip()]
                for code, rx in SIGNAL_PATTERNS.items():
                    hit = next((x for x in sentences if re.search(rx, x, re.I)), None)
                    if hit:
                        found.append(code)
                        self.add_evidence(
                            "account", acc.id, f"Signal {code}: {hit[:140]}", hit, src.id, 0.75, kind="signal"
                        )
            out.findings.append(
                PageFinding(
                    account_id=acc.id,
                    url=res["url"],
                    status=res["status"],
                    reason=res.get("reason", ""),
                    cached=res["cached"],
                    signals=found,
                )
            )
        return out


# ---------------------------------------------------------------- Buying Signal


class SignalInput(BaseModel):
    account_ids: list[str]
    researched_account_ids: list[str] = Field(default_factory=list)


class AccountSignals(BaseModel):
    account_id: str
    signals: list[str]
    evidence_ids: list[str]


class SignalOutput(AgentOutput):
    accounts: list[AccountSignals] = Field(default_factory=list)


class BuyingSignalAgent(BaseAgent):
    name = "buying_signal"
    group = "Discovery & data"
    description = "Consolidates signal evidence per account (deduplicated, freshest first) for scoring and drafting."
    tools = ()
    input_model = SignalInput
    output_model = SignalOutput
    completion_criteria = "Each researched account has a signals list (possibly empty); unresearched stay unknown."

    def run(self, inp: SignalInput) -> SignalOutput:
        out = SignalOutput(confidence=0.8)
        researched = set(inp.researched_account_ids)
        for aid in inp.account_ids:
            acc = self.session.get(Account, aid)
            if acc is None:
                continue
            evs = self.session.scalars(
                select(Evidence)
                .where(Evidence.entity_id == aid, Evidence.kind == "signal")
                .order_by(Evidence.created_at.desc())
            ).all()
            codes: dict[str, str] = {}
            for e in evs:
                code = e.claim.split(":", 1)[0].replace("Signal ", "").strip()
                codes.setdefault(code, e.id)
            if evs or aid in researched:
                acc.signals = sorted(codes)
            out.accounts.append(
                AccountSignals(account_id=aid, signals=sorted(codes), evidence_ids=list(codes.values()))
            )
        return out


# ---------------------------------------------------------------- Lead Scoring


class ScoringInput(BaseModel):
    entity: Literal["account", "lead"]
    ids: list[str]
    icp_version_id: str


class ScoreSummary(BaseModel):
    entity_id: str
    qualification_id: str
    total_score_0_100: float
    tier: str
    missing: list[str]
    disqualified_by: list[str]


class ScoringOutput(AgentOutput):
    results: list[ScoreSummary] = Field(default_factory=list)


def account_facts(session, acc: Account, icp: ICPVersion) -> tuple[dict, dict[str, list[str]]]:
    from ..models import Suppression

    contacts = session.scalars(select(Contact).where(Contact.account_id == acc.id)).all()
    roles = [c.buyer_role for c in contacts if c.buyer_role != "unknown"]
    srcs = [session.get(SourceRecord, s) for s in acc.source_ids]
    confs = [s.confidence for s in srcs if s]
    competitors = {c.lower() for c in icp.definition.get("competitors", [])}
    suppressed = (
        bool(acc.domain)
        and session.scalars(select(Suppression).where(Suppression.email_or_domain == acc.domain)).first() is not None
    )
    facts = {
        "industry": acc.industry,
        "employee_band": acc.employee_band,
        "country": acc.country,
        "region": acc.region,
        "business_model": acc.business_model,
        "buyer_roles": roles or None,
        "signals": acc.signals,
        "source_confidence": max(confs) if confs else None,
        "last_verified_at": acc.last_verified_at,
        "is_competitor": any(acc.name.lower().startswith(c) for c in competitors),
        "domain_suppressed": suppressed,
    }
    evs = session.scalars(select(Evidence).where(Evidence.entity_id == acc.id)).all()
    fact_ev = [e.id for e in evs if e.kind in ("fact", "merge")]
    sig_ev = [e.id for e in evs if e.kind == "signal"]
    role_ev = [
        e.id
        for c in contacts
        for e in session.scalars(select(Evidence).where(Evidence.entity_id == c.id, Evidence.kind == "fact"))
    ]
    evidence = {
        "industry": fact_ev,
        "company_scale": fact_ev,
        "geography": fact_ev,
        "business_model": fact_ev,
        "source_confidence": fact_ev,
        "data_freshness": fact_ev,
        "observed_signals": sig_ev,
        "buyer_role_fit": role_ev,
    }
    return facts, evidence


class LeadScoringAgent(BaseAgent):
    name = "lead_scoring"
    group = "Discovery & data"
    description = (
        "Calls the deterministic scoring engine and stores explainable QualificationResults; the LLM only "
        "writes the plain-language explanation."
    )
    tools = ("scoring.score", "llm.generate")
    input_model = ScoringInput
    output_model = ScoringOutput
    completion_criteria = "Every id has a QualificationResult with criteria contributions and a model version."

    def run(self, inp: ScoringInput) -> ScoringOutput:
        icp = self.session.get(ICPVersion, inp.icp_version_id)
        if icp is None or icp.status not in ("approved", "superseded"):
            raise PermissionError("Scoring requires an approved ICP version")
        out = ScoringOutput(confidence=0.9)
        for eid in inp.ids:
            if inp.entity == "account":
                acc = self.session.get(Account, eid)
                if acc is None:
                    continue
                facts, evidence = account_facts(self.session, acc, icp)
                target = acc
            else:
                lead = self.session.get(Lead, eid)
                if lead is None:
                    continue
                acc = self.session.get(Account, lead.account_id)
                contact = self.session.get(Contact, lead.contact_id)
                assert acc is not None and contact is not None
                facts, evidence = account_facts(self.session, acc, icp)
                facts["buyer_roles"] = [contact.buyer_role]
                facts["email_status"] = contact.email_status
                facts["contact_suppressed"] = contact.suppressed
                evidence["buyer_role_fit"] = [
                    e.id for e in self.session.scalars(select(Evidence).where(Evidence.entity_id == contact.id))
                ]
                target = lead
            disq = list(icp.hard_disqualifiers)
            if inp.entity == "lead":
                disq += [
                    {"rule": "email_invalid", "reason": "Email failed syntax check or bounced"},
                    {"rule": "contact_suppressed", "reason": "Contact is suppressed"},
                ]
            res = self.tool(
                "scoring.score",
                facts=facts,
                icp_version=icp.version,
                definition=icp.definition,
                weights=icp.weights,
                disqualifiers=disq,
                thresholds=icp.tier_thresholds,
                evidence=evidence,
            )
            crit = [c.__dict__ for c in res.criteria]
            expl = self.llm(
                "explain_score",
                {
                    "total": res.total_score_0_100,
                    "tier": res.tier,
                    "criteria": crit,
                    "hard_disqualifiers": res.hard_disqualifiers,
                },
            ).text
            qr = QualificationResult(
                entity=inp.entity,
                entity_id=eid,
                model_version=res.model_version,
                icp_version_id=icp.id,
                hard_disqualifiers=res.hard_disqualifiers,
                criteria=crit,
                total_score_0_100=res.total_score_0_100,
                tier=res.tier,
                explanation=expl,
                missing_data=res.missing_data,
            )
            self.session.add(qr)
            self.session.flush()
            target.qualification_id = qr.id
            out.evidence_ids.extend(e for c in res.criteria for e in c.evidence_ids)
            out.results.append(
                ScoreSummary(
                    entity_id=eid,
                    qualification_id=qr.id,
                    total_score_0_100=res.total_score_0_100,
                    tier=res.tier,
                    missing=res.missing_data,
                    disqualified_by=[d["rule"] for d in res.hard_disqualifiers],
                )
            )
            if len(res.missing_data) >= 3:
                out.notes.append(f"{eid}: {len(res.missing_data)} criteria missing data - review before outreach")
        return out

    def check_completion(self, out: ScoringOutput) -> list[str]:
        return [] if all(r.qualification_id for r in out.results) else ["unscored entity"]


# ---------------------------------------------------------------- Contact Discovery (+ role matching)


class ContactDiscoveryInput(BaseModel):
    icp_version_id: str
    account_ids: list[str]
    adapters: list[str] = Field(default_factory=lambda: ["licensed_contacts"])
    csv_upload_id: str | None = None


class ContactDiscoveryOutput(AgentOutput):
    contact_ids: list[str] = Field(default_factory=list)
    created: int = 0
    merged: int = 0
    role_counts: dict[str, int] = Field(default_factory=dict)
    skipped: list[Rejected] = Field(default_factory=list)


def match_buyer_role(title: str | None, personas: list[dict]) -> tuple[str, str]:
    t = (title or "").lower()
    if not t:
        return "unknown", "no title"
    order = ["economic_buyer", "champion", "technical", "user"]
    for role in order:
        for p in personas:
            if p["buyer_role"] != role:
                continue
            for kw in p["title_keywords"]:
                if re.search(rf"\b{re.escape(kw)}\b", t):
                    return role, f"title contains '{kw}' ({p['name']} persona)"
    return "unknown", "title matches no persona keyword"


class ContactDiscoveryAgent(BaseAgent):
    name = "contact_discovery"
    group = "Discovery & data"
    description = (
        "Finds contacts at selected accounts from authorised sources only (user CSV, licensed dataset) and "
        "matches titles to buyer roles from the approved personas."
    )
    tools = ("csv.read_upload", "licensed_contacts.lookup", "sources.adapter_config")
    input_model = ContactDiscoveryInput
    output_model = ContactDiscoveryOutput
    completion_criteria = "Every contact has a source record, a consent basis and a buyer-role decision with a reason."

    def run(self, inp: ContactDiscoveryInput) -> ContactDiscoveryOutput:
        icp = self.session.get(ICPVersion, inp.icp_version_id)
        assert icp is not None
        personas = icp.definition.get("personas", [])
        accounts = {a.domain: a for a in (self.session.get(Account, i) for i in inp.account_ids) if a and a.domain}
        out = ContactDiscoveryOutput(confidence=0.8)
        raw: list[tuple[str, dict, str, str]] = []  # (domain, fields, source_id, consent)
        if "csv_upload" in inp.adapters and inp.csv_upload_id:
            adapter = self.tool("sources.adapter_config", adapter_id="csv_upload")
            upload, rows = self.tool("csv.read_upload", upload_id=inp.csv_upload_id)
            for r in rows:
                ref = f"csv:{upload.filename}#row{r['_row']}"
                dom = normalise_domain(r.get("account_domain"))
                if dom not in accounts:
                    out.skipped.append(Rejected(ref=ref, reason="account not in selected list"))
                    continue
                src = self.source_record(
                    "csv_upload",
                    "csv",
                    ref,
                    {k: v for k, v in r.items() if k != "_row"},
                    0.7,
                    adapter.terms_note,
                    upload.uploaded_at,
                )
                raw.append((dom, r, src.id, "provided_by_user_csv"))
        if "licensed_contacts" in inp.adapters:
            for dom in sorted(accounts):
                for rec in self.tool("licensed_contacts.lookup", domain=dom):
                    src = self.source_record(
                        "licensed_contacts", "licensed", rec.url_or_ref, rec.payload, rec.confidence, rec.terms_note
                    )
                    raw.append((dom, rec.payload, src.id, rec.payload.get("consent_basis", "licensed_dataset")))
        for dom, f, sid, consent in raw:
            acc = accounts[dom]
            email = normalise_email(f.get("email"))
            key = contact_dedup_key(acc.id, f["full_name"], email)
            c = self.session.scalars(select(Contact).where(Contact.dedup_key == key)).first()
            role, why = match_buyer_role(f.get("title"), personas)
            if c is None:
                c = Contact(
                    account_id=acc.id,
                    full_name=f["full_name"].strip(),
                    title=f.get("title") or None,
                    email=email,
                    phone=f.get("phone") or None,
                    linkedin_url=f.get("linkedin_url") or None,
                    buyer_role=role,
                    role_match_reason=why,
                    source_ids=[sid],
                    consent_basis=consent,
                    dedup_key=key,
                    email_status="unknown",
                )
                self.session.add(c)
                self.session.flush()
                out.created += 1
                self.add_evidence(
                    "contact",
                    c.id,
                    f"{c.full_name} listed as '{c.title}' at {acc.name}",
                    f"source={sid}; role={role} ({why})",
                    sid,
                    0.7,
                    kind="fact",
                )
            elif sid not in c.source_ids:
                c.source_ids = [*c.source_ids, sid]
                out.merged += 1
                self.add_evidence(
                    "contact", c.id, "Duplicate contact record merged", f"dedup key {key}", sid, 0.9, kind="merge"
                )
            out.contact_ids.append(c.id)
        out.contact_ids = sorted(set(out.contact_ids))
        for cid in out.contact_ids:
            r = self.session.get(Contact, cid).buyer_role  # type: ignore[union-attr]
            out.role_counts[r] = out.role_counts.get(r, 0) + 1
        return out

    def check_completion(self, out: ContactDiscoveryOutput) -> list[str]:
        bad = [
            cid
            for cid in out.contact_ids
            if not (c := self.session.get(Contact, cid)) or not c.source_ids or not c.consent_basis
        ]
        return [f"{len(bad)} contacts lack provenance/consent"] if bad else []


# ---------------------------------------------------------------- Contact Verification


class VerificationInput(BaseModel):
    contact_ids: list[str]


class VerificationResult(BaseModel):
    contact_id: str
    email: str | None
    email_status: str
    risky: bool
    checks: list[dict]


class VerificationOutput(AgentOutput):
    results: list[VerificationResult] = Field(default_factory=list)
    status_counts: dict[str, int] = Field(default_factory=dict)


class ContactVerificationAgent(BaseAgent):
    name = "contact_verification"
    group = "Discovery & data"
    description = (
        "Runs syntax -> domain MX -> provider checks and records the highest level honestly reached. "
        "Never marks an address deliverable: only a delivery event can."
    )
    tools = ("verification.verify_email",)
    input_model = VerificationInput
    output_model = VerificationOutput
    completion_criteria = "Every contact has an email_status and the list of checks performed (with mock flags)."

    def run(self, inp: VerificationInput) -> VerificationOutput:
        out = VerificationOutput(confidence=0.9)
        for cid in inp.contact_ids:
            c = self.session.get(Contact, cid)
            if c is None:
                continue
            if c.email_status in ("deliverable_confirmed", "bounced"):
                res_status, checks, risky = c.email_status, c.verification_checks, False  # event-derived: keep
            else:
                v = self.tool("verification.verify_email", email=c.email)
                res_status, checks, risky = v.status, v.checks, v.risky
                c.email_status = res_status
                c.verification_checks = checks
                c.last_verified_at = utcnow()
            out.results.append(
                VerificationResult(contact_id=cid, email=c.email, email_status=res_status, risky=risky, checks=checks)
            )
            out.status_counts[res_status] = out.status_counts.get(res_status, 0) + 1
        return out

    def check_completion(self, out: VerificationOutput) -> list[str]:
        return [] if all(r.checks or r.email_status != "unknown" for r in out.results) else ["unverified contact"]
