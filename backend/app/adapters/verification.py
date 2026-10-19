"""DNS and mailbox-verification adapters.

MockDNS / MockVerifier read fixtures (the synthetic example.* domains have no real MX records). They are labelled
mock everywhere their results are shown. DnsPythonMX is an optional real lookup (needs the `dns` extra; untested)."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache

from ..config import FIXTURES_DIR, get_settings


@lru_cache
def _fixture() -> dict:
    return json.loads((FIXTURES_DIR / "verification.json").read_text())


class MockDNS:
    name = "mock-dns"
    mock = True

    def has_mx(self, domain: str) -> bool | None:
        fx = _fixture()
        if domain in fx["dns_error_domains"]:
            return None
        return domain not in fx["no_mx_domains"]


class DnsPythonMX:  # pragma: no cover - optional, needs network; not exercised by tests
    name = "dnspython"
    mock = False

    def has_mx(self, domain: str) -> bool | None:
        try:
            import dns.resolver  # type: ignore

            return len(dns.resolver.resolve(domain, "MX", lifetime=3.0)) > 0
        except Exception as e:  # noqa: BLE001
            return False if e.__class__.__name__ in ("NXDOMAIN", "NoAnswer") else None


class MockVerifier:
    """Deterministic mock mailbox verifier: fixture overrides, catch-all domains, else hash-based valid/unknown."""

    name = "mock-verifier"
    mock = True

    def verify(self, email: str) -> str:
        fx = _fixture()
        e = email.lower()
        if e in fx["provider_overrides"]:
            return fx["provider_overrides"][e]
        if e.split("@", 1)[1] in fx["catch_all_domains"]:
            return "catch_all"
        return "valid" if int(hashlib.sha256(e.encode()).hexdigest(), 16) % 10 < 8 else "unknown"


def get_dns():
    return DnsPythonMX() if get_settings().dns_provider == "dnspython" else MockDNS()


def get_verifier():
    return MockVerifier()
