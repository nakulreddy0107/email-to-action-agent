"""Calendar Specialist Agent.

Translates a schedule_meeting intent into a PlannedAction for the Calendar tool.
Uses an LLM call only when entities are underspecified (e.g., missing title).
"""
from __future__ import annotations

import uuid

from core.models import DetectedIntent, PlannedAction, IntentType


class CalendarAgent:
    name = "calendar"

    def plan(self, intent: DetectedIntent, email_subject: str) -> PlannedAction:
        e = intent.entities
        title = e.get("title") or f"Meeting: {email_subject}"
        attendees = e.get("attendees") or []
        if isinstance(attendees, str):
            attendees = [attendees]
        proposed_time = e.get("proposed_time")
        duration = int(e.get("duration_minutes") or 30)
        location = e.get("location_or_link")

        # Require approval if attendees are missing or time is vague
        requires_approval = (
            not attendees
            or not proposed_time
            or intent.confidence < 0.7
        )

        return PlannedAction(
            action_id=str(uuid.uuid4()),
            intent_type=IntentType.SCHEDULE_MEETING,
            tool="calendar",
            payload={
                "title": title,
                "attendees": attendees,
                "proposed_time": proposed_time,
                "duration_minutes": duration,
                "location_or_link": location,
            },
            confidence=intent.confidence,
            requires_approval=requires_approval,
            rationale=f"Scheduling because: {intent.summary}",
        )
