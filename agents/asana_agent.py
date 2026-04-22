"""Asana Specialist Agent.

Turns a create_task intent into a PlannedAction for Asana.
"""
from __future__ import annotations

import uuid

from core.models import DetectedIntent, PlannedAction, IntentType


class AsanaAgent:
    name = "asana"

    def plan(self, intent: DetectedIntent, email_subject: str, sender: str) -> PlannedAction:
        e = intent.entities
        name = e.get("title") or intent.summary or f"Task from email: {email_subject}"
        notes_lines = [
            f"Source email: {email_subject}",
            f"Requested by: {sender}",
            "",
            e.get("description") or intent.raw_excerpt or intent.summary,
        ]
        notes = "\n".join([ln for ln in notes_lines if ln is not None])

        requires_approval = intent.confidence < 0.7

        return PlannedAction(
            action_id=str(uuid.uuid4()),
            intent_type=IntentType.CREATE_TASK,
            tool="asana",
            payload={
                "name": name,
                "notes": notes,
                "priority": str(e.get("priority", "")).lower() or None,
                "assignee": e.get("assignee"),
                "due_on": e.get("due_date"),
                "project_gid": None,       # fall back to .env default
                "workspace_gid": None,     # fall back to .env default
            },
            confidence=intent.confidence,
            requires_approval=requires_approval,
            rationale=f"Creating Asana task because: {intent.summary}",
        )