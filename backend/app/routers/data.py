"""Sources, offers, ICP versions, accounts, contacts, leads, suppressions, evidence and shared-interface exports."""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import approvals, audit, outreach
from ..adapters.sources import AdapterError, parse_csv
from ..config import SAMPLES_DIR
from ..db import get_session
from ..models import (
    Account,
    AdapterUsage,
    Approval,
    Contact,
    CsvUpload,
    Evidence,
    FetchCache,
    ICPVersion,
    Lead,
    Offer,
    QualificationResult,
    ResearchArtifact,
    SourceAdapter,
    SourceRecord,
    Suppression,
)
from ..schemas import Account as AccountOut
from ..schemas import Contact as ContactOut
from ..schemas import Evidence as EvidenceOut
from ..schemas import Lead as LeadOut
from ..schemas import QualificationResult as QROut
from ..schemas import SourceRecord as SourceOut
from ..schemas import Suppression as SuppressionOut
from ..scoring import CRITERIA, model_version
from ..security import Actor, require
from .util import to_dict

router = APIRouter(prefix="/api")
MAX_CSV_BYTES = 2_000_000


# ---------------------------------------------------------------- sources


@router.get("/sources")
def list_sources(session: Session = Depends(get_session), _: Actor = Depends(require("read"))) -> list[dict]:
    out = []
    for a in session.scalars(select(SourceAdapter).order_by(SourceAdapter.id)):
        n = session.scalar(select(func.count()).select_from(SourceRecord).where(SourceRecord.adapter == a.id)) or 0
        used = session.scalar(select(func.sum(AdapterUsage.count)).where(AdapterUsage.adapter == a.id)) or 0
        out.append({**to_dict(a), "records": n, "requests_total": used})
    return out


class AdapterPatch(BaseModel):
    enabled: bool | None = None
    allowlist: list[str] | None = None
    blocklist: list[str] | None = None
    rate_limit_per_minute: int | None = Field(None, ge=1, le=10000)
    retention_days: int | None = Field(None, ge=1, le=3650)


@router.patch("/sources/{adapter_id}")
def patch_source(
    adapter_id: str,
    body: AdapterPatch,
    session: Session = Depends(get_session),
    actor: Actor = Depends(require("sources.configure")),
) -> dict:
    a = session.get(SourceAdapter, adapter_id)
    if a is None:
        raise HTTPException(404, "adapter not found")
    before = to_dict(a)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(a, k, [x.strip().lower() for x in v if x.strip()] if k in ("allowlist", "blocklist") else v)
    audit.record(session, actor, "source.configured", "source_adapter", a.id, before=before, after=to_dict(a))
    session.commit()
    return to_dict(a)


@router.get("/sources/records")
def source_records(
    adapter: str | None = None,
    limit: int = 200,
    session: Session = Depends(get_session),
    _: Actor = Depends(require("read")),
) -> list[dict]:
    q = select(SourceRecord).order_by(SourceRecord.retrieved_at.desc()).limit(min(limit, 1000))
    if adapter:
        q = q.where(SourceRecord.adapter == adapter)
    return [SourceOut.model_validate(r).model_dump(mode="json") for r in session.scalars(q)]


@router.get("/sources/cache")
def fetch_cache(session: Session = Depends(get_session), _: Actor = Depends(require("read"))) -> list[dict]:
    return [
        {"url": c.url, "status": c.status, "fetched_at": c.fetched_at.isoformat(), "bytes": len(c.content)}
        for c in session.scalars(select(FetchCache).order_by(FetchCache.fetched_at.desc()))
    ]


@router.get("/sources/csv")
def list_uploads(session: Session = Depends(get_session), _: Actor = Depends(require("read"))) -> list[dict]:
    return [
        to_dict(u, exclude={"content"})
        for u in session.scalars(select(CsvUpload).order_by(CsvUpload.uploaded_at.desc()))
    ]


@router.post("/sources/csv")
async def upload_csv(
    kind: str = Form(...),
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    actor: Actor = Depends(require("sources.import")),
) -> dict:
    if kind not in ("accounts", "contacts"):
        raise HTTPException(422, "kind must be accounts or contacts")
    raw = await file.read(MAX_CSV_BYTES + 1)
    if len(raw) > MAX_CSV_BYTES:
        raise HTTPException(413, "CSV larger than 2 MB")
    try:
        content = raw.decode("utf-8-sig")
        rows = parse_csv(content, kind)
    except (UnicodeDecodeError, AdapterError) as e:
        raise HTTPException(422, f"Invalid CSV: {e}") from e
    up = CsvUpload(
        filename=(file.filename or "upload.csv")[:200],
        kind=kind,
        content=content,
        sha256=hashlib.sha256(raw).hexdigest(),
        row_count=len(rows),
        uploaded_by=actor.actor_id,
    )
    session.add(up)
    session.flush()
    audit.record(
        session,
        actor,
        "source.csv_uploaded",
        "csv_upload",
        up.id,
        after={"filename": up.filename, "kind": kind, "rows": len(rows), "sha256": up.sha256},
    )
    session.commit()
    return to_dict(up, exclude={"content"})


@router.get("/sources/samples/{name}", response_class=PlainTextResponse)
def sample_csv(name: str) -> str:
    if name not in ("accounts.csv", "contacts.csv"):
        raise HTTPException(404, "unknown sample")
    return (SAMPLES_DIR / name).read_text()


# ---------------------------------------------------------------- offers & ICP


@router.get("/offers")
def offers(session: Session = Depends(get_session), _: Actor = Depends(require("read"))) -> list[dict]:
    return [to_dict(o) for o in session.scalars(select(Offer))]


@router.get("/icp")
def icp_versions(session: Session = Depends(get_session), _: Actor = Depends(require("read"))) -> list[dict]:
    out = []
    for i in session.scalars(select(ICPVersion).order_by(ICPVersion.version.desc())):
        out.append(
            {
                **to_dict(i),
                "model_version": model_version(
                    i.version, i.definition, i.weights, i.hard_disqualifiers, i.tier_thresholds
                ),
            }
        )
    return out


@router.get("/icp/{icp_id}")
def icp_detail(icp_id: str, session: Session = Depends(get_session), _: Actor = Depends(require("read"))) -> dict:
    i = session.get(ICPVersion, icp_id)
    if i is None:
        raise HTTPException(404, "ICP not found")
    research = session.scalars(
        select(ResearchArtifact).where(ResearchArtifact.offer_id == i.offer_id).order_by(ResearchArtifact.created_at)
    ).all()
    ev_ids = {e for r in research for e in r.evidence_ids}
    evidence = (
        [
            EvidenceOut.model_validate(e).model_dump(mode="json")
            for e in session.scalars(select(Evidence).where(Evidence.id.in_(ev_ids)))
        ]
        if ev_ids
        else []
    )
    pending = session.scalars(select(Approval).where(Approval.subject_id == i.id, Approval.status == "pending")).first()
    return {
        **to_dict(i),
        "criteria": CRITERIA,
        "model_version": model_version(i.version, i.definition, i.weights, i.hard_disqualifiers, i.tier_thresholds),
        "research": [to_dict(r) for r in research],
        "evidence": evidence,
        "pending_approval_id": pending.id if pending else None,
    }


class ICPPatch(BaseModel):
    weights: dict[str, float] | None = None
    tier_thresholds: dict[str, float] | None = None
    industries: list[str] | None = None
    adjacent_industries: list[str] | None = None
    countries: list[str] | None = None
    employee_bands: list[str] | None = None
    hard_disqualifiers: list[dict] | None = None
    change_summary: str | None = None


@router.post("/icp/{icp_id}/fork")
def fork_icp(icp_id: str, session: Session = Depends(get_session), actor: Actor = Depends(require("icp.edit"))) -> dict:
    src = session.get(ICPVersion, icp_id)
    if src is None:
        raise HTTPException(404, "ICP not found")
    version = (session.scalar(select(func.max(ICPVersion.version))) or 0) + 1
    new = ICPVersion(
        version=version,
        status="draft",
        offer_id=src.offer_id,
        name=f"ICP v{version} (edited from v{src.version})",
        definition=dict(src.definition),
        weights=dict(src.weights),
        hard_disqualifiers=list(src.hard_disqualifiers),
        tier_thresholds=dict(src.tier_thresholds),
        rationale=src.rationale,
        parent_version_id=src.id,
        created_by=actor.label,
    )
    session.add(new)
    session.flush()
    audit.record(session, actor, "icp.forked", "icp_version", new.id, after={"from": src.id, "version": version})
    session.commit()
    return to_dict(new)


@router.patch("/icp/{icp_id}")
def patch_icp(
    icp_id: str, body: ICPPatch, session: Session = Depends(get_session), actor: Actor = Depends(require("icp.edit"))
) -> dict:
    i = session.get(ICPVersion, icp_id)
    if i is None:
        raise HTTPException(404, "ICP not found")
    if i.status != "draft":
        raise HTTPException(409, "Only draft ICP versions can be edited; fork a new version instead")
    before = {"weights": i.weights, "definition": i.definition, "tier_thresholds": i.tier_thresholds}
    if body.weights is not None:
        unknown = set(body.weights) - set(CRITERIA)
        if unknown or any(v < 0 for v in body.weights.values()) or sum(body.weights.values()) <= 0:
            raise HTTPException(422, "weights must use known criteria, be >= 0 and sum to > 0")
        i.weights = {c: float(body.weights.get(c, 0)) for c in CRITERIA}
    if body.tier_thresholds is not None:
        a, b = body.tier_thresholds.get("A", 80), body.tier_thresholds.get("B", 60)
        if not 0 < b < a <= 100:
            raise HTTPException(422, "need 0 < B < A <= 100")
        i.tier_thresholds = {"A": a, "B": b}
    d = dict(i.definition)
    for k in ("industries", "adjacent_industries", "countries", "employee_bands"):
        v = getattr(body, k)
        if v is not None:
            d[k] = v
    i.definition = d
    if body.hard_disqualifiers is not None:
        i.hard_disqualifiers = body.hard_disqualifiers
    if body.change_summary is not None:
        i.change_summary = body.change_summary
    audit.record(
        session,
        actor,
        "icp.edited",
        "icp_version",
        i.id,
        before=before,
        after={"weights": i.weights, "definition": i.definition, "tier_thresholds": i.tier_thresholds},
    )
    session.commit()
    return to_dict(i)


@router.post("/icp/{icp_id}/submit")
def submit_icp(
    icp_id: str, session: Session = Depends(get_session), actor: Actor = Depends(require("icp.edit"))
) -> dict:
    i = session.get(ICPVersion, icp_id)
    if i is None:
        raise HTTPException(404, "ICP not found")
    if i.status != "draft":
        raise HTTPException(409, f"ICP is {i.status}")
    i.status = "pending_approval"
    apr = approvals.request(
        session,
        subject_type="icp_version",
        subject_id=i.id,
        action="icp.update",
        requested_by=actor,
        initiated_by=actor.actor_id,
        summary=f"{i.name}: {i.change_summary or 'manual edit'}",
    )
    session.commit()
    return {"approval_id": apr.id, "status": i.status}


# ---------------------------------------------------------------- accounts & contacts


def _qr(session: Session, qid: str | None) -> dict | None:
    q = session.get(QualificationResult, qid) if qid else None
    if q is None:
        return None
    return {
        **QROut.model_validate(q).model_dump(mode="json"),
        "explanation": q.explanation,
        "missing_data": q.missing_data,
        "icp_version_id": q.icp_version_id,
    }


@router.get("/accounts")
def accounts(
    tier: str | None = None,
    q: str | None = None,
    session: Session = Depends(get_session),
    _: Actor = Depends(require("read")),
) -> list[dict]:
    out = []
    for a in session.scalars(select(Account).order_by(Account.name)):
        if q and q.lower() not in (a.name + " " + (a.domain or "")).lower():
            continue
        qr = session.get(QualificationResult, a.qualification_id) if a.qualification_id else None
        if tier and (qr is None or qr.tier != tier):
            continue
        n = session.scalar(select(func.count()).select_from(Contact).where(Contact.account_id == a.id)) or 0
        out.append(
            {
                **AccountOut.model_validate(a).model_dump(mode="json"),
                "signals": a.signals,
                "contacts": n,
                "score": qr.total_score_0_100 if qr else None,
                "tier": qr.tier if qr else None,
                "model_version": qr.model_version if qr else None,
                "missing_data": qr.missing_data if qr else [],
                "synthetic": a.synthetic,
            }
        )
    return out


@router.get("/accounts/{account_id}")
def account_detail(
    account_id: str, session: Session = Depends(get_session), _: Actor = Depends(require("read"))
) -> dict:
    a = session.get(Account, account_id)
    if a is None:
        raise HTTPException(404, "account not found")
    contacts = session.scalars(select(Contact).where(Contact.account_id == a.id).order_by(Contact.full_name)).all()
    evidence = session.scalars(select(Evidence).where(Evidence.entity_id == a.id).order_by(Evidence.created_at)).all()
    sources = [session.get(SourceRecord, s) for s in a.source_ids]
    history = session.scalars(
        select(QualificationResult)
        .where(QualificationResult.entity_id == a.id)
        .order_by(QualificationResult.explained_at.desc())
    ).all()
    return {
        "account": {**AccountOut.model_validate(a).model_dump(mode="json"), "signals": a.signals},
        "qualification": _qr(session, a.qualification_id),
        "score_history": [
            {
                "id": h.id,
                "total": h.total_score_0_100,
                "tier": h.tier,
                "model_version": h.model_version,
                "at": h.explained_at.isoformat(),
            }
            for h in history
        ],
        "evidence": [
            {**EvidenceOut.model_validate(e).model_dump(mode="json"), "kind": e.kind, "agent": e.agent}
            for e in evidence
        ],
        "sources": [SourceOut.model_validate(s).model_dump(mode="json") for s in sources if s],
        "contacts": [ContactOut.model_validate(c).model_dump(mode="json") for c in contacts],
    }


@router.get("/contacts")
def contacts(
    status: str | None = None,
    role: str | None = None,
    session: Session = Depends(get_session),
    _: Actor = Depends(require("read")),
) -> list[dict]:
    q = select(Contact).order_by(Contact.full_name)
    if status:
        q = q.where(Contact.email_status == status)
    if role:
        q = q.where(Contact.buyer_role == role)
    out = []
    for c in session.scalars(q):
        acc = session.get(Account, c.account_id)
        lead = session.scalars(select(Lead).where(Lead.contact_id == c.id)).first()
        out.append(
            {
                **ContactOut.model_validate(c).model_dump(mode="json"),
                "account_name": acc.name if acc else None,
                "verification_checks": c.verification_checks,
                "role_match_reason": c.role_match_reason,
                "lead_stage": lead.stage if lead else None,
                "lead_id": lead.id if lead else None,
                "lead_qualification": _qr(session, lead.qualification_id) if lead else None,
            }
        )
    return out


@router.post("/contacts/{contact_id}/erase")
def erase_contact(
    contact_id: str, session: Session = Depends(get_session), actor: Actor = Depends(require("contacts.erase"))
) -> dict:
    """Data-subject erasure: suppress the address (so it is never re-imported/contacted) then delete the record."""
    c = session.get(Contact, contact_id)
    if c is None:
        raise HTTPException(404, "contact not found")
    if c.email:
        outreach.add_suppression(session, c.email, "legal", actor, note="erasure request")
    audit.record(
        session, actor, "contact.erased", "contact", c.id, before={"account_id": c.account_id}, severity="warn"
    )
    session.delete(c)
    session.commit()
    return {"erased": contact_id}


@router.get("/leads")
def leads(session: Session = Depends(get_session), _: Actor = Depends(require("read"))) -> list[dict]:
    return [
        LeadOut.model_validate(x).model_dump(mode="json") | {"segment": x.segment}
        for x in session.scalars(select(Lead).order_by(Lead.created_at))
    ]


# ---------------------------------------------------------------- suppression


@router.get("/suppressions")
def suppressions(session: Session = Depends(get_session), _: Actor = Depends(require("read"))) -> list[dict]:
    return [
        SuppressionOut.model_validate(s).model_dump(mode="json") | {"note": s.note, "created_by": s.created_by}
        for s in session.scalars(select(Suppression).order_by(Suppression.created_at.desc()))
    ]


class SuppressionIn(BaseModel):
    email_or_domain: str = Field(min_length=3, max_length=200)
    reason: str = "manual"
    note: str = ""


@router.post("/suppressions")
def add_suppression(
    body: SuppressionIn, session: Session = Depends(get_session), actor: Actor = Depends(require("suppressions.manage"))
) -> dict:
    try:
        s = outreach.add_suppression(session, body.email_or_domain, body.reason, actor, body.note)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    session.commit()
    return SuppressionOut.model_validate(s).model_dump(mode="json")


# ---------------------------------------------------------------- evidence & export


@router.get("/evidence/{evidence_id}")
def evidence(evidence_id: str, session: Session = Depends(get_session), _: Actor = Depends(require("read"))) -> dict:
    e = session.get(Evidence, evidence_id)
    if e is None:
        raise HTTPException(404, "evidence not found")
    src = session.get(SourceRecord, e.source_id) if e.source_id else None
    return {
        **EvidenceOut.model_validate(e).model_dump(mode="json"),
        "kind": e.kind,
        "agent": e.agent,
        "source": SourceOut.model_validate(src).model_dump(mode="json") if src else None,
    }


@router.get("/export/shared")
def export_shared(session: Session = Depends(get_session), _: Actor = Depends(require("read"))) -> dict:
    """Shared-interface export (accounts, contacts, leads, qualification results, sources, suppressions) that
    autonomous-b2b-revenue-ops can import. All records are synthetic."""
    return {
        "schema": "leadgen-shared-v1",
        "synthetic": True,
        "accounts": [AccountOut.model_validate(a).model_dump(mode="json") for a in session.scalars(select(Account))],
        "contacts": [ContactOut.model_validate(c).model_dump(mode="json") for c in session.scalars(select(Contact))],
        "leads": [LeadOut.model_validate(x).model_dump(mode="json") for x in session.scalars(select(Lead))],
        "qualification_results": [
            QROut.model_validate(q).model_dump(mode="json") for q in session.scalars(select(QualificationResult))
        ],
        "source_records": [
            SourceOut.model_validate(s).model_dump(mode="json") for s in session.scalars(select(SourceRecord))
        ],
        "suppressions": [
            SuppressionOut.model_validate(s).model_dump(mode="json") for s in session.scalars(select(Suppression))
        ],
    }
