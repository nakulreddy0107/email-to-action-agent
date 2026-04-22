"""Streaming wrapper around Orchestrator.

Re-implements the pipeline as a generator that yields events at each stage so
the UI can render agent activity live via Server-Sent Events.

Why re-implement instead of patching Orchestrator? Keeps the sync API clean and
avoids threading callbacks through every agent. The streaming version is only
used by the SSE endpoint.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterator

from agents.calendar_agent import CalendarAgent
from agents.ingestion import IngestionAgent
from agents.intent_detection import IntentDetectionAgent
from agents.asana_agent import AsanaAgent
from agents.policy import PolicyAgent
from agents.reply_agent import ReplyAgent
from agents.slack_agent import SlackAgent
from core.executor import ActionExecutor
from core.llm import start_capture, stop_capture
from core.models import (
    ActionStatus,
    ExecutionResult,
    IntentType,
    PlannedAction,
)
from core.store import init_db, log_action, log_email, log_intent


def _event(stage: str, agent: str, status: str, **data: Any) -> dict[str, Any]:
    return {
        "stage": stage,
        "agent": agent,
        "status": status,
        "ts": datetime.utcnow().isoformat(),
        **data,
    }


class StreamingOrchestrator:
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
        # Pending actions awaiting human approval, keyed by action_id
        self.pending: dict[str, PlannedAction] = {}

    def stream(self, email_data: dict[str, Any]) -> Iterator[dict[str, Any]]:
        yield _event("start", "orchestrator", "running", message="Starting pipeline")

        # ----- 1. Ingestion -----
        yield _event("ingest", "ingestion", "running", message="Cleaning email content")
        email = self.ingestion.ingest_from_dict(email_data)
        log_email(email.message_id, email.sender, email.subject, email.body, email.received_at)
        yield _event(
            "ingest",
            "ingestion",
            "done",
            email_id=email.message_id,
            sender=email.sender,
            sender_name=email.sender_name,
            subject=email.subject,
            body_preview=email.body[:200],
        )

        # ----- 2. Intent detection -----
        yield _event(
            "intent",
            "intent_detection",
            "running",
            message="Asking LLM to decompose into atomic intents",
        )
        capture = start_capture()
        try:
            analysis = self.intent.analyze(email)
        except Exception as e:
            stop_capture()
            yield _event("intent", "intent_detection", "error", error=str(e))
            yield _event("end", "orchestrator", "error", error=str(e))
            return
        stop_capture()
        intent_llm_call = capture[0] if capture else None

        for it in analysis.intents:
            log_intent(email.message_id, it.intent_type.value, it.summary, it.confidence, it.entities)

        yield _event(
            "intent",
            "intent_detection",
            "done",
            intent_count=len(analysis.intents),
            sentiment=analysis.overall_sentiment,
            reasoning=analysis.reasoning,
            intents=[
                {
                    "type": i.intent_type.value,
                    "summary": i.summary,
                    "confidence": i.confidence,
                    "entities": i.entities,
                    "raw_excerpt": i.raw_excerpt,
                }
                for i in analysis.intents
            ],
            llm_call=intent_llm_call,
        )

        # ----- 3. Planning -----
        planned_actions: list[PlannedAction] = []
        reply_intents = []
        planned_summaries: list[str] = []

        for intent in analysis.intents:
            agent_name: str | None = None
            action: PlannedAction | None = None

            if intent.intent_type == IntentType.SCHEDULE_MEETING:
                agent_name = "calendar"
                action = self.calendar_agent.plan(intent, email.subject)
            elif intent.intent_type == IntentType.CREATE_TASK:
                agent_name = "asana"
                action = self.asana_agent.plan(intent, email.subject, email.sender)
            elif intent.intent_type == IntentType.NOTIFY_TEAM:
                agent_name = "slack"
                action = self.slack_agent.plan(intent, email.subject, email.sender_name)
            elif intent.intent_type == IntentType.SEND_REPLY:
                reply_intents.append(intent)
                continue
            else:
                yield _event(
                    "plan",
                    "orchestrator",
                    "skipped",
                    intent_type=intent.intent_type.value,
                    message=f"No specialist for {intent.intent_type.value}",
                )
                continue

            planned_actions.append(action)
            planned_summaries.append(f"{action.tool}: {action.rationale}")
            yield _event(
                "plan",
                agent_name,
                "done",
                action_id=action.action_id,
                tool=action.tool,
                intent_type=action.intent_type.value,
                confidence=action.confidence,
                requires_approval=action.requires_approval,
                payload=action.payload,
                rationale=action.rationale,
            )

        # Replies planned last, after we know what else will happen
        for intent in reply_intents:
            yield _event("plan", "reply", "running", message="Drafting reply with LLM")
            reply_capture = start_capture()
            try:
                action = self.reply_agent.plan(intent, email, planned_summaries)
            except Exception as e:
                stop_capture()
                yield _event("plan", "reply", "error", error=str(e))
                continue
            stop_capture()
            reply_llm_call = reply_capture[0] if reply_capture else None
            planned_actions.append(action)
            yield _event(
                "plan",
                "reply",
                "done",
                action_id=action.action_id,
                tool=action.tool,
                intent_type=action.intent_type.value,
                confidence=action.confidence,
                requires_approval=action.requires_approval,
                payload=action.payload,
                rationale=action.rationale,
                llm_call=reply_llm_call,
            )

        # ----- 4. Policy + execute -----
        for action in planned_actions:
            verdict = self.policy.evaluate(action)
            yield _event(
                "policy",
                "policy",
                "done",
                action_id=action.action_id,
                tool=action.tool,
                verdict=verdict.verdict,
                reason=verdict.reason,
            )

            if verdict.verdict == "reject":
                result = ExecutionResult(
                    action_id=action.action_id,
                    status=ActionStatus.SKIPPED_POLICY,
                    message=verdict.reason,
                )
            elif verdict.verdict == "require_human":
                # Park it for human approval and expose via /actions/pending
                self.pending[action.action_id] = action
                result = ExecutionResult(
                    action_id=action.action_id,
                    status=ActionStatus.PENDING,
                    message=f"Awaiting human approval: {verdict.reason}",
                    raw_response=action.payload,
                )
            else:
                yield _event(
                    "execute",
                    action.tool,
                    "running",
                    action_id=action.action_id,
                    message=f"Calling {action.tool} API",
                )
                result = self.executor.execute(action)

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

            yield _event(
                "execute",
                action.tool,
                "done",
                action_id=action.action_id,
                outcome=result.status.value,
                external_id=result.external_id,
                external_url=result.external_url,
                message=result.message,
            )

        yield _event(
            "end",
            "orchestrator",
            "done",
            email_id=email.message_id,
            total_actions=len(planned_actions),
        )

    # -- Human-in-the-loop --
    def approve(self, action_id: str) -> ExecutionResult:
        action = self.pending.pop(action_id, None)
        if action is None:
            raise KeyError(f"No pending action with id {action_id}")
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

    def reject(self, action_id: str) -> None:
        action = self.pending.pop(action_id, None)
        if action is None:
            raise KeyError(f"No pending action with id {action_id}")
        log_action(
            action_id=action.action_id,
            email_id="(manual-rejection)",
            tool=action.tool,
            intent_type=action.intent_type.value,
            payload=action.payload,
            status=ActionStatus.SKIPPED_POLICY.value,
            confidence=action.confidence,
            message="Rejected by human reviewer",
        )

    def list_pending(self) -> list[dict[str, Any]]:
        return [
            {
                "action_id": a.action_id,
                "tool": a.tool,
                "intent_type": a.intent_type.value,
                "confidence": a.confidence,
                "payload": a.payload,
                "rationale": a.rationale,
            }
            for a in self.pending.values()
        ]
