"""Mock CRM adapter: writes idempotent upserts to the crm_outbox table instead of calling a CRM API."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CrmOutbox


def upsert(session: Session, entity_type: str, entity_id: str, operation: str, payload: dict, key: str) -> bool:
    if session.scalars(select(CrmOutbox).where(CrmOutbox.idempotency_key == key)).first():
        return False
    session.add(
        CrmOutbox(
            entity_type=entity_type, entity_id=entity_id, operation=operation, payload=payload, idempotency_key=key
        )
    )
    session.flush()
    return True
