"""Tool registry. Agents reach adapters, the DB and deterministic engines only through these named tools, and only
the names listed in their `tools` tuple (enforced in BaseAgent.tool)."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from sqlalchemy import select

from .. import approvals, outreach, scoring, verification
from ..adapters import crm, events, sources
from ..adapters.verification import get_dns, get_verifier
from ..config import FIXTURES_DIR
from ..models import CsvUpload, KnowledgePassage, SourceAdapter, Suppression


def _sections(md: str) -> dict[str, str]:
    out: dict[str, str] = {}
    cur = None
    for line in md.splitlines():
        if line.startswith("## "):
            cur = line[3:].strip()
            out[cur] = ""
        elif cur:
            out[cur] += line.strip() + " "
    return {k: v.strip() for k, v in out.items()}


def t_market_notes(agent) -> dict[str, str]:
    return _sections((FIXTURES_DIR / "market" / "segments.md").read_text())


def t_competitor_notes(agent) -> dict[str, str]:
    return _sections((FIXTURES_DIR / "market" / "competitors.md").read_text())


def t_registry_search(agent, industries: list[str], countries: list[str]) -> list[sources.RawRecord]:
    return sources.registry_search(
        agent.session, sources.get_adapter(agent.session, "mock_registry"), industries, countries
    )


def t_csv_read(agent, upload_id: str) -> tuple[CsvUpload, list[dict]]:
    up = agent.session.get(CsvUpload, upload_id)
    if up is None:
        raise sources.AdapterError(f"CSV upload {upload_id} not found")
    return up, sources.parse_csv(up.content, up.kind)


def t_adapter(agent, adapter_id: str) -> SourceAdapter:
    return sources.get_adapter(agent.session, adapter_id)


def t_licensed_contacts(agent, domain: str) -> list[sources.RawRecord]:
    return sources.licensed_contacts_for(agent.session, sources.get_adapter(agent.session, "licensed_contacts"), domain)


def t_fetch_page(agent, domain: str, path: str = "/news") -> dict:
    return sources.fetch_public_page(
        agent.session, sources.get_adapter(agent.session, "public_web_fixture"), domain, path
    )


def t_verify_email(agent, email: str | None) -> verification.VerificationOutcome:
    return verification.verify_email(email, get_dns(), get_verifier())


def t_score(agent, **kwargs) -> scoring.ScoreResult:
    return scoring.score(**kwargs)


def t_llm(agent, task: str, payload: dict):
    return agent.ctx.llm.generate(task, payload)


def t_suppression_check(agent, contact) -> outreach.SuppressionCheck:
    return outreach.check_suppression(agent.session, contact)


def t_domain_suppressed(agent, domain: str | None) -> bool:
    if not domain:
        return False
    return agent.session.scalars(select(Suppression).where(Suppression.email_or_domain == domain)).first() is not None


def t_add_suppression(agent, value: str, reason: str, note: str = "") -> Any:
    return outreach.add_suppression(agent.session, value, reason, agent.actor, note)


def t_cancel_followups(agent, contact_id: str, reason: str) -> int:
    return outreach.cancel_pending_for_contact(agent.session, contact_id, reason, agent.actor, min_step=2)


def t_queue(agent, campaign) -> dict:
    return outreach.queue_campaign(agent.session, campaign, agent.actor)


def t_dispatch(agent, campaign) -> dict:
    return outreach.dispatch_due(agent.session, campaign, agent.actor)


def t_request_approval(agent, **kwargs):
    return approvals.request(agent.session, requested_by=agent.actor, **kwargs)


def t_simulated_events(agent, sends) -> dict:
    return events.simulate_for_sends(agent.session, sends)


def t_crm_upsert(agent, **kwargs) -> bool:
    return crm.upsert(agent.session, **kwargs)


_WORD = re.compile(r"[a-z]{3,}")
_STOP = {
    "the",
    "and",
    "for",
    "you",
    "your",
    "our",
    "with",
    "does",
    "this",
    "that",
    "can",
    "are",
    "from",
    "what",
    "how",
    "before",
    "further",
    "looks",
    "relevant",
    "mostly",
    "work",
    "thanks",
    "hi",
    "could",
}


def t_knowledge_search(agent, question: str, min_overlap: int = 2) -> list[dict]:
    """Keyword-overlap retrieval over APPROVED passages only. Returns passages with >= min_overlap shared terms."""
    q = {w for w in _WORD.findall(question.lower()) if w not in _STOP}
    hits = []
    for p in agent.session.scalars(select(KnowledgePassage).where(KnowledgePassage.approved.is_(True))):
        terms = set(_WORD.findall((p.text + " " + " ".join(p.topics)).lower()))
        overlap = sorted(q & terms)
        if len(overlap) >= min_overlap:
            hits.append(
                {
                    "id": p.id,
                    "doc": p.doc,
                    "section": p.section,
                    "text": p.text,
                    "version": p.version,
                    "overlap": overlap,
                }
            )
    return sorted(hits, key=lambda h: -len(h["overlap"]))


TOOLS: dict[str, Callable[..., Any]] = {
    "fixtures.market_notes": t_market_notes,
    "fixtures.competitor_notes": t_competitor_notes,
    "registry.search": t_registry_search,
    "csv.read_upload": t_csv_read,
    "sources.adapter_config": t_adapter,
    "licensed_contacts.lookup": t_licensed_contacts,
    "web.fetch_public_page": t_fetch_page,
    "verification.verify_email": t_verify_email,
    "scoring.score": t_score,
    "llm.generate": t_llm,
    "suppression.check": t_suppression_check,
    "suppression.domain_check": t_domain_suppressed,
    "suppression.add": t_add_suppression,
    "outreach.cancel_followups": t_cancel_followups,
    "outreach.queue": t_queue,
    "outreach.dispatch": t_dispatch,
    "approvals.request": t_request_approval,
    "events.simulate": t_simulated_events,
    "crm.upsert": t_crm_upsert,
    "knowledge.search": t_knowledge_search,
}
