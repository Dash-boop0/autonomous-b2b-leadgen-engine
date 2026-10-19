"""Simulated mailbox/ESP events for the dry-run campaign, plus idempotent ingestion of any event.

simulate_for_sends() derives a deterministic scenario per contact from fixtures/reply_scenarios.json, and the
provider_event_id is deterministic, so simulating twice never creates duplicate events."""

from __future__ import annotations

import json
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import FIXTURES_DIR
from ..models import MessageEvent, SendQueueItem

PROVIDER = "dry-run-simulator"
EVENT_TYPES = {"delivered", "bounced", "replied", "unsubscribed", "complaint"}


@lru_cache
def scenarios() -> dict:
    return json.loads((FIXTURES_DIR / "reply_scenarios.json").read_text())


def ingest(
    session: Session,
    *,
    provider: str,
    provider_event_id: str,
    event_type: str,
    send_id: str | None,
    contact_id: str | None,
    payload: dict | None = None,
    simulated: bool = True,
) -> tuple[MessageEvent, bool]:
    """Insert an event once. Returns (event, created). Duplicates (same provider + provider_event_id) are ignored."""
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown event_type {event_type}")
    existing = session.scalars(
        select(MessageEvent).where(
            MessageEvent.provider == provider, MessageEvent.provider_event_id == provider_event_id
        )
    ).first()
    if existing:
        return existing, False
    ev = MessageEvent(
        provider=provider,
        provider_event_id=provider_event_id,
        event_type=event_type,
        send_id=send_id,
        contact_id=contact_id,
        payload=payload or {},
        simulated=simulated,
    )
    try:
        with session.begin_nested():
            session.add(ev)
            session.flush()
    except IntegrityError:
        return session.scalars(
            select(MessageEvent).where(
                MessageEvent.provider == provider, MessageEvent.provider_event_id == provider_event_id
            )
        ).one(), False
    return ev, True


def simulate_for_sends(session: Session, sends: list[SendQueueItem]) -> dict:
    sc = scenarios()
    created = dup = 0
    for s in sends:
        if s.status != "sent_simulated":
            continue
        name = sc["by_email"].get(s.to_email.lower(), sc["default"]) if s.step_no == 1 else sc["followup_default"]
        for ev in sc["scenarios"][name]:
            payload = {"scenario": name, "step": s.step_no}
            if ev == "replied":
                payload["body"] = sc["reply_bodies"][name]
            _, was_new = ingest(
                session,
                provider=PROVIDER,
                provider_event_id=f"{s.id}:{ev}",
                event_type=ev,
                send_id=s.id,
                contact_id=s.contact_id,
                payload=payload,
            )
            created += was_new
            dup += not was_new
    return {"created": created, "duplicates_ignored": dup}
