"""Pydantic data models used across all agents.

Structured models are the contract between agents — every agent returns one of
these types, which makes the orchestrator deterministic and testable.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------- Inbound email ----------

class EmailMessage(BaseModel):
    """Raw inbound email after ingestion + cleaning."""
    message_id: str
    sender: str
    sender_name: Optional[str] = None
    recipients: list[str] = Field(default_factory=list)
    subject: str
    body: str
    received_at: datetime = Field(default_factory=datetime.utcnow)
    thread_id: Optional[str] = None


# ---------- Intent detection ----------

class IntentType(str, Enum):
    SCHEDULE_MEETING = "schedule_meeting"
    CREATE_TASK = "create_task"
    NOTIFY_TEAM = "notify_team"
    SEND_REPLY = "send_reply"
    INFORMATIONAL = "informational"
    UNKNOWN = "unknown"


class DetectedIntent(BaseModel):
    """Single atomic intent extracted from an email."""
    intent_type: IntentType
    summary: str = Field(..., description="One-line description of what to do")
    confidence: float = Field(..., ge=0.0, le=1.0)
    entities: dict[str, Any] = Field(
        default_factory=dict,
        description="Extracted params (attendees, dates, channel, priority, etc.)",
    )
    raw_excerpt: Optional[str] = Field(
        None, description="The sentence/passage from the email this intent came from"
    )


class IntentAnalysis(BaseModel):
    """The full set of intents found in one email."""
    email_id: str
    intents: list[DetectedIntent]
    overall_sentiment: Literal["neutral", "positive", "urgent", "negative"] = "neutral"
    reasoning: Optional[str] = None


# ---------- Action execution ----------

class ActionStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    EXECUTED = "executed"
    FAILED = "failed"
    SKIPPED_LOW_CONFIDENCE = "skipped_low_confidence"
    SKIPPED_POLICY = "skipped_policy"
    DRY_RUN = "dry_run"


class PlannedAction(BaseModel):
    """An action planned by a specialist agent, ready for execution."""
    action_id: str
    intent_type: IntentType
    tool: Literal["asana", "slack", "calendar", "email_reply"]
    payload: dict[str, Any]
    confidence: float
    requires_approval: bool = False
    rationale: str


class ExecutionResult(BaseModel):
    """Result of executing a PlannedAction."""
    action_id: str
    status: ActionStatus
    external_id: Optional[str] = None
    external_url: Optional[str] = None
    message: str = ""
    executed_at: datetime = Field(default_factory=datetime.utcnow)
    raw_response: dict[str, Any] = Field(default_factory=dict)


# ---------- Full workflow output ----------

class WorkflowResult(BaseModel):
    """End-to-end output — what the orchestrator returns per email."""
    email_id: str
    subject: str
    intents: list[DetectedIntent]
    actions: list[PlannedAction]
    results: list[ExecutionResult]
    audit_trail: list[str] = Field(default_factory=list)
    started_at: datetime
    finished_at: datetime
