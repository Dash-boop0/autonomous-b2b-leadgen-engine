"""Demo authentication and deterministic role-based permissions.

This is DEMO auth: seeded users share the documented password "demo" and tokens are HMAC-signed, stateless and
short-lived. Replace with a real identity provider before any non-demo use (see docs/SECURITY.md)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_session
from .models import User

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "viewer": frozenset({"read"}),
    "operator": frozenset(
        {
            "read",
            "workflows.run",
            "sources.import",
            "icp.edit",
            "campaigns.manage",
            "events.simulate",
            "suppressions.manage",
        }
    ),
    "reviewer": frozenset({"read", "approvals.decide", "replies.triage", "suppressions.manage", "workflows.run"}),
    "admin": frozenset(
        {
            "read",
            "workflows.run",
            "sources.import",
            "sources.configure",
            "icp.edit",
            "campaigns.manage",
            "events.simulate",
            "suppressions.manage",
            "approvals.decide",
            "replies.triage",
            "contacts.erase",
            "system.stop",
            "system.reset",
        }
    ),
}

# Permissions an agent may never hold, whatever configuration says.
AGENT_FORBIDDEN = frozenset({"approvals.decide", "system.stop", "system.reset", "sources.configure", "contacts.erase"})


@dataclass(frozen=True)
class Actor:
    actor_type: str  # human | agent | system
    actor_id: str
    role: str | None = None
    display_name: str | None = None

    def can(self, permission: str) -> bool:
        if self.actor_type == "agent":
            return permission not in AGENT_FORBIDDEN and permission in {"read", "workflows.run"}
        if self.actor_type == "system":
            return permission not in AGENT_FORBIDDEN
        return permission in ROLE_PERMISSIONS.get(self.role or "", frozenset())

    @property
    def label(self) -> str:
        return f"{self.actor_type}:{self.actor_id}"


SYSTEM = Actor("system", "system")


def agent_actor(name: str) -> Actor:
    return Actor("agent", name)


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(8)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000).hex()
    return f"pbkdf2${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$", 2)
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(password, salt).split("$", 2)[2], digest)


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_token(user: User) -> str:
    s = get_settings()
    body = _b64(
        json.dumps({"sub": user.id, "role": user.role, "exp": int(time.time()) + s.token_ttl_minutes * 60}).encode()
    )
    sig = _b64(hmac.new(s.secret_key.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def read_token(token: str) -> dict | None:
    try:
        body, sig = token.split(".", 1)
    except ValueError:
        return None
    expected = _b64(hmac.new(get_settings().secret_key.encode(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    data = json.loads(_unb64(body))
    if data.get("exp", 0) < time.time():
        return None
    return data


def current_actor(authorization: str | None = Header(default=None), session: Session = Depends(get_session)) -> Actor:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Not signed in")
    data = read_token(authorization.split(" ", 1)[1])
    if not data:
        raise HTTPException(401, "Session expired or invalid")
    user = session.get(User, data["sub"])
    if not user or not user.active:
        raise HTTPException(401, "Unknown user")
    # Role is always read from the DB, never trusted from the token.
    return Actor("human", user.id, user.role, user.display_name)


def require(permission: str):
    def dep(actor: Actor = Depends(current_actor)) -> Actor:
        if not actor.can(permission):
            raise HTTPException(403, f"Role '{actor.role}' lacks permission '{permission}'")
        return actor

    return dep
