"""First-request bootstrap: run Alembic migrations and seed if the DB is empty.

Idempotent and safe for concurrent cold starts: on PostgreSQL the whole step runs under a session-level advisory
lock, and 'seed if empty' is re-checked inside the lock. On SQLite (single local process) a thread lock suffices."""

from __future__ import annotations

import logging
import threading

from alembic.config import Config
from sqlalchemy import text

from alembic import command

from .config import BACKEND_DIR, get_settings
from .db import engine, session_factory

log = logging.getLogger(__name__)
ADVISORY_LOCK_KEY = 74_240_001
_lock = threading.Lock()
_ready = False


def alembic_config() -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return cfg


def migrate(connection=None) -> None:
    cfg = alembic_config()
    if connection is not None:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "head")
        return
    with engine().begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")


def _seed_if_empty() -> bool:
    from .seed import is_seeded, seed

    s = session_factory()()
    try:
        if is_seeded(s):
            return False
        seed(s, get_settings().seed_profile)
        return True
    finally:
        s.close()


def migrate_and_seed() -> bool:
    eng = engine()
    if eng.dialect.name == "postgresql":
        with eng.connect() as lock_conn:
            lock_conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": ADVISORY_LOCK_KEY})
            lock_conn.commit()
            try:
                migrate()
                return _seed_if_empty()
            finally:
                lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": ADVISORY_LOCK_KEY})
                lock_conn.commit()
    migrate()
    return _seed_if_empty()


def ensure_ready() -> None:
    global _ready
    if _ready or not get_settings().auto_bootstrap:
        return
    with _lock:
        if _ready:
            return
        seeded = migrate_and_seed()
        log.info("bootstrap complete (seeded=%s)", seeded)
        _ready = True


def reset_flag() -> None:
    global _ready
    _ready = False


def reset_demo(profile: str = "basic") -> dict:
    """Wipe and re-seed (admin action). Runs under the same advisory lock on PostgreSQL."""
    from .seed import seed, wipe

    def _do() -> dict:
        s = session_factory()()
        try:
            wipe(s)
            return seed(s, profile)
        finally:
            s.close()

    eng = engine()
    if eng.dialect.name == "postgresql":
        with eng.connect() as lock_conn:
            lock_conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": ADVISORY_LOCK_KEY})
            lock_conn.commit()
            try:
                return _do()
            finally:
                lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": ADVISORY_LOCK_KEY})
                lock_conn.commit()
    return _do()
