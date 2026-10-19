"""Deterministic normalisation and de-duplication keys for accounts and contacts."""

from __future__ import annotations

import hashlib
import json
import re

_SUFFIXES = re.compile(
    r"\b(ltd|limited|plc|llc|inc|incorporated|corp|corporation|gmbh|bv|b\.v\.|nv|sa|sas|ag|co|company|group|holdings)\.?$"
)

COUNTRY_ALIASES = {
    "uk": "GB",
    "united kingdom": "GB",
    "great britain": "GB",
    "england": "GB",
    "scotland": "GB",
    "wales": "GB",
    "gb": "GB",
    "ireland": "IE",
    "ie": "IE",
    "netherlands": "NL",
    "the netherlands": "NL",
    "holland": "NL",
    "nl": "NL",
    "germany": "DE",
    "de": "DE",
    "france": "FR",
    "fr": "FR",
    "belgium": "BE",
    "be": "BE",
    "spain": "ES",
    "es": "ES",
    "united states": "US",
    "usa": "US",
    "us": "US",
    "denmark": "DK",
    "dk": "DK",
}
COUNTRY_REGION = {
    "GB": "Europe",
    "IE": "Europe",
    "NL": "Europe",
    "DE": "Europe",
    "FR": "Europe",
    "BE": "Europe",
    "ES": "Europe",
    "DK": "Europe",
    "US": "North America",
}

EMPLOYEE_BANDS = ["1-10", "11-50", "51-200", "201-500", "501-1000", "1001-5000", "5001+"]
_BAND_LIMITS = [(10, "1-10"), (50, "11-50"), (200, "51-200"), (500, "201-500"), (1000, "501-1000"), (5000, "1001-5000")]


def normalise_domain(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip().lower()
    v = re.sub(r"^[a-z]+://", "", v)
    v = v.split("/", 1)[0].split("?", 1)[0].split(":", 1)[0]
    if v.startswith("www."):
        v = v[4:]
    return v or None


def normalise_company_name(name: str) -> str:
    n = name.strip().lower().replace("&", " and ")
    n = re.sub(r"[^\w\s.]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    prev = None
    while prev != n:
        prev = n
        n = _SUFFIXES.sub("", n).strip(" .")
    return n


def normalise_country(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip()
    return COUNTRY_ALIASES.get(v.lower(), v.upper() if len(v) == 2 else None)


def employee_band(value: str | int | None) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        v = value.strip().replace(",", "")
        if v in EMPLOYEE_BANDS:
            return v
        if not v.isdigit():
            m = re.match(r"^(\d+)\s*[-–]\s*(\d+)$", v)
            if m:
                return employee_band(int(m.group(2)))
            return None
        value = int(v)
    for limit, band in _BAND_LIMITS:
        if value <= limit:
            return band
    return "5001+"


def account_dedup_key(name: str, domain: str | None, country: str | None) -> str:
    d = normalise_domain(domain)
    if d:
        return f"domain:{d}"
    return f"name:{normalise_company_name(name)}|{(country or '').upper()}"


def normalise_email(email: str | None) -> str | None:
    if not email:
        return None
    return email.strip().lower() or None


def contact_dedup_key(account_id: str, full_name: str, email: str | None) -> str:
    e = normalise_email(email)
    if e:
        return f"email:{e}"
    return f"name:{account_id}|{re.sub(r'\s+', ' ', full_name.strip().lower())}"


def raw_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
