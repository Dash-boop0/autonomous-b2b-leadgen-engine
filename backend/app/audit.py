"""Append-only audit trail. Every agent action, approval, state change, send and error goes through record()."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .models import AuditEvent
from .security import Actor


def _jsonable(v: Any) -> Any:
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_jsonable(x) for x in v]
    if hasattr(v, "model_dump"):
        return _jsonable(v.model_dump(mode="json"))
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def record(
    session: Session,
    actor: Actor,
    action: str,
    subject_type: str | None = None,
    subject_id: str | None = None,
    *,
    before: Any = None,
    after: Any = None,
    evidence_ids: list[str] | None = None,
    run_id: str | None = None,
    step_id: str | None = None,
    severity: str = "info",
) -> AuditEvent:
    ev = AuditEvent(
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action=action,
        subject_type=subject_type,
        subject_id=subject_id,
        before=_jsonable(before),
        after=_jsonable(after),
        evidence_ids=list(evidence_ids or []),
        run_id=run_id,
        step_id=step_id,
        severity=severity,
    )
    session.add(ev)
    session.flush()
    return ev
