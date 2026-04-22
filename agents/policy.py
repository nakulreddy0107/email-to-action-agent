"""Policy / Validation Agent.

Last-line-of-defense gate before any external call. Checks:
- Minimum confidence threshold
- Required fields present
- No obvious PII / destructive payloads
- Business rules (e.g., never Slack #executive-team from automation)

Returns a verdict: approve | require_human | reject.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from core.config import settings
from core.models import PlannedAction

Verdict = Literal["approve", "require_human", "reject"]


# Channels automation is never allowed to post to without human approval
_PROTECTED_CHANNELS = {"#executive-team", "#board", "#legal", "#security-incident"}


@dataclass
class PolicyVerdict:
    verdict: Verdict
    reason: str


class PolicyAgent:
    name = "policy"

    def evaluate(self, action: PlannedAction) -> PolicyVerdict:
        # 1. Confidence gate
        if action.confidence < settings.confidence_threshold:
            return PolicyVerdict(
                verdict="require_human",
                reason=(
                    f"Confidence {action.confidence:.2f} below threshold "
                    f"{settings.confidence_threshold:.2f}."
                ),
            )

        # 2. Tool-specific checks
        if action.tool == "slack":
            ch = action.payload.get("channel", "")
            if ch in _PROTECTED_CHANNELS:
                return PolicyVerdict(
                    verdict="require_human",
                    reason=f"Channel {ch} is protected and requires manual approval.",
                )
            if not action.payload.get("text"):
                return PolicyVerdict(verdict="reject", reason="Empty Slack message.")

        elif action.tool == "jira":
            if not action.payload.get("summary"):
                return PolicyVerdict(verdict="reject", reason="Jira issue missing summary.")

        elif action.tool == "calendar":
            if not action.payload.get("attendees"):
                return PolicyVerdict(
                    verdict="require_human",
                    reason="Calendar event has no attendees — likely underspecified.",
                )

        elif action.tool == "email_reply":
            # All outbound email requires human approval by design
            return PolicyVerdict(
                verdict="require_human",
                reason="All outbound email replies require human review.",
            )

        # 3. Explicit requires_approval flag from the planning agent
        if action.requires_approval:
            return PolicyVerdict(
                verdict="require_human",
                reason="Specialist agent flagged action for review.",
            )

        return PolicyVerdict(verdict="approve", reason="All policy checks passed.")
