"""Campaign metrics with explicit numerators and denominators (pure functions, unit-tested).

All rates are contact-level so a contact who gets several messages is counted once:
  delivery_rate        = delivered_contacts   / contacts_sent
  bounce_rate          = bounced_contacts     / contacts_sent
  reply_rate           = replied_contacts     / delivered_contacts   (human replies only: excludes out-of-office
                                                                      auto-replies and unsubscribe-only requests)
  positive_reply_rate  = interested_contacts  / replied_contacts
  unsubscribe_rate     = unsubscribed_contacts/ delivered_contacts
A rate whose denominator is 0 is reported as None ("n/a"), never as 0%.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass

AUTO_OR_NON_CONVERSATION = {"out_of_office", "unsubscribe", "bounce_notice"}
POSITIVE = {"interested", "referral"}


@dataclass
class Ratio:
    numerator: int
    denominator: int
    value: float | None
    formula: str


def ratio(num: int, den: int, formula: str) -> Ratio:
    return Ratio(num, den, None if den == 0 else round(num / den, 4), formula)


def campaign_metrics(sends: Iterable[dict], events: Iterable[dict], replies: Iterable[dict]) -> dict:
    sends, events, replies = list(sends), list(events), list(replies)
    sent = [s for s in sends if s["status"] == "sent_simulated"]
    contacts_sent = {s["contact_id"] for s in sent}
    by_type: dict[str, set] = defaultdict(set)
    for e in events:
        if e.get("contact_id") in contacts_sent:
            by_type[e["event_type"]].add(e["contact_id"])
    delivered = by_type["delivered"] - by_type["bounced"]
    replied = {r["contact_id"] for r in replies if r["classification"] not in AUTO_OR_NON_CONVERSATION} & contacts_sent
    interested = {r["contact_id"] for r in replies if r["classification"] in POSITIVE} & replied
    unsub = (
        by_type["unsubscribed"] | {r["contact_id"] for r in replies if r["classification"] == "unsubscribe"}
    ) & delivered
    cancelled_followups = sum(1 for s in sends if s["status"] == "cancelled" and s["step_no"] > 1)
    return {
        "counts": {
            "messages_sent": len(sent),
            "contacts_sent": len(contacts_sent),
            "delivered_contacts": len(delivered),
            "bounced_contacts": len(by_type["bounced"]),
            "replied_contacts": len(replied),
            "interested_contacts": len(interested),
            "unsubscribed_contacts": len(unsub),
            "followups_cancelled": cancelled_followups,
            "blocked_sends": sum(1 for s in sends if s["status"] == "blocked"),
        },
        "rates": {
            k: asdict(v)
            for k, v in {
                "delivery_rate": ratio(len(delivered), len(contacts_sent), "delivered_contacts / contacts_sent"),
                "bounce_rate": ratio(len(by_type["bounced"]), len(contacts_sent), "bounced_contacts / contacts_sent"),
                "reply_rate": ratio(len(replied), len(delivered), "replied_contacts / delivered_contacts"),
                "positive_reply_rate": ratio(len(interested), len(replied), "interested_contacts / replied_contacts"),
                "unsubscribe_rate": ratio(len(unsub), len(delivered), "unsubscribed_contacts / delivered_contacts"),
            }.items()
        },
    }


def segment_breakdown(
    sends: list[dict], events: list[dict], replies: list[dict], key: Callable[[str], str]
) -> list[dict]:
    """Group the same metrics by a contact-level key (e.g. industry, tier, source adapter)."""
    groups: dict[str, set] = defaultdict(set)
    for s in sends:
        groups[key(s["contact_id"])].add(s["contact_id"])
    out = []
    for seg, cids in sorted(groups.items()):
        m = campaign_metrics(
            [s for s in sends if s["contact_id"] in cids],
            [e for e in events if e.get("contact_id") in cids],
            [r for r in replies if r["contact_id"] in cids],
        )
        out.append({"segment": seg, **m})
    return out
