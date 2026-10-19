"""Email verification levels. Each level is a different, honest claim:

  syntax_valid / syntax_invalid  - the string parses as an address (local check only)
  domain_has_mx                  - the domain publishes MX records (DNS signal; says nothing about the mailbox)
  provider_verified              - a verification provider said the mailbox looks valid (in the demo: MOCK provider)
  deliverable_confirmed          - a real delivery event was received for a sent message (in the demo: SIMULATED)
  bounced                        - a bounce event was received

Verification never sets deliverable_confirmed; only a delivery event can."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import utcnow

EMAIL_STATUSES = [
    "unknown",
    "syntax_invalid",
    "syntax_valid",
    "domain_has_mx",
    "provider_verified",
    "deliverable_confirmed",
    "bounced",
]
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9-]{1,63})+$")


def syntax_ok(email: str | None) -> bool:
    if not email or len(email) > 254 or email.count("@") != 1:
        return False
    local = email.split("@", 1)[0]
    if not local or len(local) > 64 or ".." in email or local.startswith(".") or local.endswith("."):
        return False
    return bool(_EMAIL_RE.match(email))


@dataclass
class VerificationOutcome:
    status: str
    checks: list[dict] = field(default_factory=list)
    risky: bool = False


def verify_email(email: str | None, dns, verifier) -> VerificationOutcome:
    """dns: object with has_mx(domain)->bool|None and .name/.mock; verifier: .verify(email)->str and .name/.mock."""
    at = utcnow().isoformat()
    checks: list[dict] = []
    if not syntax_ok(email):
        checks.append({"check": "syntax", "result": "fail", "provider": "local-regex", "mock": False, "at": at})
        return VerificationOutcome("syntax_invalid", checks)
    assert email is not None
    checks.append({"check": "syntax", "result": "pass", "provider": "local-regex", "mock": False, "at": at})
    domain = email.split("@", 1)[1].lower()
    mx = dns.has_mx(domain)
    checks.append(
        {
            "check": "domain_mx",
            "result": {True: "pass", False: "fail", None: "error"}[mx],
            "provider": dns.name,
            "mock": dns.mock,
            "at": at,
        }
    )
    if not mx:
        return VerificationOutcome("syntax_valid", checks, risky=True)
    result = verifier.verify(email)  # valid | invalid | catch_all | unknown
    checks.append({"check": "provider", "result": result, "provider": verifier.name, "mock": verifier.mock, "at": at})
    if result == "valid":
        return VerificationOutcome("provider_verified", checks)
    return VerificationOutcome("domain_has_mx", checks, risky=result == "invalid")


def status_after_event(current: str, event_type: str) -> str:
    if event_type == "bounced":
        return "bounced"
    if event_type == "delivered" and current != "bounced":
        return "deliverable_confirmed"
    return current
