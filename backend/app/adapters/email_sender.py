"""Email sending adapter. Only the dry-run sender exists: it records the message in the send_queue/outbox table and
returns a synthetic message id. There is deliberately no SMTP or ESP implementation in this repository."""

from __future__ import annotations

import hashlib


class DryRunSender:
    name = "dry-run-outbox"
    mode = "dry_run"
    mock = True

    def send(self, *, to: str, subject: str, body: str, ref: str) -> str:
        return "dryrun-" + hashlib.sha256(f"{ref}|{to}|{subject}".encode()).hexdigest()[:16]


def get_sender() -> DryRunSender:
    return DryRunSender()
