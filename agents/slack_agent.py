"""Slack Specialist Agent.

Turns a notify_team intent into a PlannedAction for Slack.
"""
from __future__ import annotations

import uuid

from core.config import settings
from core.models import DetectedIntent, PlannedAction, IntentType


class SlackAgent:
    name = "slack"

    def plan(self, intent: DetectedIntent, email_subject: str, sender_name: str | None) -> PlannedAction:
        e = intent.entities
        channel = e.get("channel") or settings.slack_default_channel
        if channel and not channel.startswith("#") and not channel.startswith("C"):
            channel = f"#{channel}"
        
        base_msg = e.get("message") or intent.summary
        attribution = f"(via email from {sender_name or 'unknown sender'} — subject: \"{email_subject}\")"
        message = f"{base_msg}\n{attribution}"

        urgency = str(e.get("urgency", "medium")).lower()
        mentions = e.get("mentions") or []
        if isinstance(mentions, str):
            mentions = [mentions]

        requires_approval = intent.confidence < 0.6  # notifications are lower-stakes

        return PlannedAction(
            action_id=str(uuid.uuid4()),
            intent_type=IntentType.NOTIFY_TEAM,
            tool="slack",
            payload={
                "channel": channel,
                "text": message,
                "mentions": mentions,
                "urgency": urgency,
            },
            confidence=intent.confidence,
            requires_approval=requires_approval,
            rationale=f"Notifying {channel} because: {intent.summary}",
        )
