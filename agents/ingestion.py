"""Ingestion Agent.

Responsibilities:
- Accept an email from any source (IMAP, webhook, Gmail API, or a local .eml/.json file)
- Normalize + clean content (strip signatures, quoted replies, trackers)
- Emit a clean EmailMessage pydantic object

In production you'd swap `ingest_from_dict` for a Gmail/Outlook fetcher. For the
demo + tests, we accept dicts so the pipeline is trivially exercisable.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

from core.models import EmailMessage


# Lines after these markers are usually quoted prior replies
_QUOTE_MARKERS = [
    r"^On .+wrote:$",
    r"^-{2,}\s*Original Message\s*-{2,}",
    r"^From: .+",
    r"^>{1,}",
]
_QUOTE_RE = re.compile("|".join(_QUOTE_MARKERS), re.MULTILINE)

# Simple signature cutoff
_SIG_RE = re.compile(r"^-- ?$", re.MULTILINE)


def _clean_body(body: str) -> str:
    """Strip quoted replies and signatures. Conservative — only trim obvious things."""
    # Cut at first quote marker
    m = _QUOTE_RE.search(body)
    if m:
        body = body[: m.start()]
    # Cut at signature marker
    m = _SIG_RE.search(body)
    if m:
        body = body[: m.start()]
    # Collapse excessive whitespace
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()


def _stable_id(sender: str, subject: str, body: str) -> str:
    h = hashlib.sha1(f"{sender}|{subject}|{body[:200]}".encode()).hexdigest()
    return f"email_{h[:12]}"


class IngestionAgent:
    name = "ingestion"

    def ingest_from_dict(self, data: dict[str, Any]) -> EmailMessage:
        """Accept a dict with keys: sender, subject, body, [recipients], [received_at]."""
        sender = data["sender"]
        subject = data.get("subject", "(no subject)")
        body = _clean_body(data.get("body", ""))
        recipients = data.get("recipients", [])
        received_at = data.get("received_at")
        if isinstance(received_at, str):
            received_at = datetime.fromisoformat(received_at)
        elif received_at is None:
            received_at = datetime.utcnow()

        # Extract sender name from "Name <email>" format
        sender_name = None
        m = re.match(r"^(.+?)\s*<(.+?)>$", sender)
        if m:
            sender_name = m.group(1).strip().strip('"')
            sender = m.group(2).strip()

        return EmailMessage(
            message_id=data.get("message_id") or _stable_id(sender, subject, body),
            sender=sender,
            sender_name=sender_name,
            recipients=recipients,
            subject=subject,
            body=body,
            received_at=received_at,
            thread_id=data.get("thread_id"),
        )
