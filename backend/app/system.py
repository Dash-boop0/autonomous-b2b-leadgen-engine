"""Global system state: emergency stop and simulated clock. Checked deterministically before any workflow tick or send."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from . import audit
from .models import SystemState, utcnow
from .security import Actor


class EmergencyStopActive(RuntimeError):
    pass


def get_state(session: Session) -> SystemState:
    st = session.get(SystemState, 1)
    if st is None:
        st = SystemState(id=1, emergency_stop=False, send_mode="dry_run", sim_clock_offset_days=0)
        session.add(st)
        session.flush()
    return st


def assert_not_stopped(session: Session) -> None:
    st = get_state(session)
    if st.emergency_stop:
        raise EmergencyStopActive(f"Emergency stop is active: {st.stop_reason or 'no reason given'}")


def set_stop(session: Session, actor: Actor, on: bool, reason: str | None) -> SystemState:
    if not actor.can("system.stop"):
        raise PermissionError("Only admins can toggle the emergency stop")
    st = get_state(session)
    before = {"emergency_stop": st.emergency_stop}
    st.emergency_stop = on
    st.stop_reason = reason if on else None
    st.stop_set_by = actor.actor_id
    st.stop_set_at = utcnow()
    audit.record(
        session,
        actor,
        "system.emergency_stop." + ("on" if on else "off"),
        "system",
        "1",
        before=before,
        after={"emergency_stop": on, "reason": reason},
        severity="warn" if on else "info",
    )
    return st


def sim_now(session: Session) -> datetime:
    """Wall clock plus the simulated offset (dry-run only: lets a demo make follow-ups due without waiting days)."""
    return utcnow() + timedelta(days=get_state(session).sim_clock_offset_days)
