"""Orchestrator.

The top-level coordinator. Given a raw email dict, runs the full pipeline:

    ingest → detect intents → plan actions (specialists) → policy check → execute

Everything is logged to the SQLite audit store.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from agents.calendar_agent import CalendarAgent
from agents.ingestion import IngestionAgent
from agents.intent_detection import IntentDetectionAgent
from agents.asana_agent import AsanaAgent
from agents.policy import PolicyAgent
from agents.reply_agent import ReplyAgent
from agents.slack_agent import SlackAgent
from core.executor import ActionExecutor
from core.models import (
    ActionStatus,
    EmailMessage,
    ExecutionResult,
    IntentType,
    PlannedAction,
    WorkflowResult,
)
from core.store import init_db, log_action, log_email, log_intent

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self) -> None:
        init_db()
        self.ingestion = IngestionAgent()
        self.intent = IntentDetectionAgent()
        self.calendar_agent = CalendarAgent()
        self.asana_agent = AsanaAgent()
        self.slack_agent = SlackAgent()
        self.reply_agent = ReplyAgent()
        self.policy = PolicyAgent()
        self.executor = ActionExecutor()

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------
    def run(self, email_data: dict[str, Any]) -> WorkflowResult:
        started_at = datetime.utcnow()
        audit: list[str] = []

        # 1. Ingest
        email = self.ingestion.ingest_from_dict(email_data)
        audit.append(f"[ingest] id={email.message_id} subject={email.subject!r}")
        log_email(email.message_id, email.sender, email.subject, email.body, email.received_at)

        # 2. Detect intents
        analysis = self.intent.analyze(email)
        audit.append(
            f"[intent] found {len(analysis.intents)} intent(s) "
            f"sentiment={analysis.overall_sentiment}"
        )
        for it in analysis.intents:
            log_intent(email.message_id, it.intent_type.value, it.summary, it.confidence, it.entities)
            audit.append(
                f"  - {it.intent_type.value} (conf={it.confidence:.2f}): {it.summary}"
            )

        # 3. Plan actions per intent
        planned_actions: list[PlannedAction] = []
        planned_summaries: list[str] = []
        reply_intents = []

        for intent in analysis.intents:
            if intent.intent_type == IntentType.SCHEDULE_MEETING:
                planned_actions.append(self.calendar_agent.plan(intent, email.subject))
            elif intent.intent_type == IntentType.CREATE_TASK:
                planned_actions.append(self.asana_agent.plan(intent, email.subject, email.sender))
            elif intent.intent_type == IntentType.NOTIFY_TEAM:
                planned_actions.append(
                    self.slack_agent.plan(intent, email.subject, email.sender_name)
                )
            elif intent.intent_type == IntentType.SEND_REPLY:
                # Handle replies last, after we know what else we're doing
                reply_intents.append(intent)
            # informational / unknown → no action

        # Build a human summary of the planned actions to feed the reply drafter
        for a in planned_actions:
            planned_summaries.append(f"{a.tool}: {a.rationale}")

        # Now plan replies with full context
        for intent in reply_intents:
            planned_actions.append(
                self.reply_agent.plan(intent, email, planned_summaries)
            )

        audit.append(f"[plan] generated {len(planned_actions)} action(s)")

        # 4. Policy check + execute
        results: list[ExecutionResult] = []
        for action in planned_actions:
            verdict = self.policy.evaluate(action)
            audit.append(
                f"[policy] {action.tool} action → {verdict.verdict} ({verdict.reason})"
            )

            if verdict.verdict == "reject":
                result = ExecutionResult(
                    action_id=action.action_id,
                    status=ActionStatus.SKIPPED_POLICY,
                    message=verdict.reason,
                )
            elif verdict.verdict == "require_human":
                result = ExecutionResult(
                    action_id=action.action_id,
                    status=ActionStatus.PENDING,
                    message=f"Awaiting human approval: {verdict.reason}",
                    raw_response=action.payload,
                )
            else:
                result = self.executor.execute(action)
                audit.append(
                    f"[execute] {action.tool} → {result.status.value} "
                    f"{result.external_id or ''} {result.external_url or ''}"
                )

            log_action(
                action_id=action.action_id,
                email_id=email.message_id,
                tool=action.tool,
                intent_type=action.intent_type.value,
                payload=action.payload,
                status=result.status.value,
                confidence=action.confidence,
                external_id=result.external_id,
                external_url=result.external_url,
                message=result.message,
                executed_at=result.executed_at,
            )
            results.append(result)

        finished_at = datetime.utcnow()
        return WorkflowResult(
            email_id=email.message_id,
            subject=email.subject,
            intents=analysis.intents,
            actions=planned_actions,
            results=results,
            audit_trail=audit,
            started_at=started_at,
            finished_at=finished_at,
        )

    # ------------------------------------------------------------------
    # Human-in-the-loop approval
    # ------------------------------------------------------------------
    def approve_and_execute(self, action: PlannedAction) -> ExecutionResult:
        """Called when a human approves a previously-pending action."""
        result = self.executor.execute(action)
        log_action(
            action_id=action.action_id,
            email_id="(manual-approval)",
            tool=action.tool,
            intent_type=action.intent_type.value,
            payload=action.payload,
            status=result.status.value,
            confidence=action.confidence,
            external_id=result.external_id,
            external_url=result.external_url,
            message=result.message,
            executed_at=result.executed_at,
        )
        return result
