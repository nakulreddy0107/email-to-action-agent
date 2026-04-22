"""Action Executor.

Takes an approved PlannedAction and dispatches it to the correct integration
client. Returns an ExecutionResult regardless of outcome (success or failure) so
the orchestrator can always record what happened.
"""
from __future__ import annotations

import logging
from datetime import datetime

from core.models import ActionStatus, ExecutionResult, PlannedAction
from integrations.calendar_client import CalendarClient
from integrations.asana_client import AsanaClient
from integrations.slack_client import SlackClient

logger = logging.getLogger(__name__)


class ActionExecutor:
    def __init__(self) -> None:
        self.asana = AsanaClient()
        self.slack = SlackClient()
        self.calendar = CalendarClient()

    def execute(self, action: PlannedAction) -> ExecutionResult:
        try:
            if action.tool == "asana":
                return self._exec_asana(action)
            if action.tool == "slack":
                return self._exec_slack(action)
            if action.tool == "calendar":
                return self._exec_calendar(action)
            if action.tool == "email_reply":
                return self._exec_reply(action)
            return ExecutionResult(
                action_id=action.action_id,
                status=ActionStatus.FAILED,
                message=f"Unknown tool: {action.tool}",
            )
        except Exception as e:
            logger.exception("Action execution failed: %s", e)
            return ExecutionResult(
                action_id=action.action_id,
                status=ActionStatus.FAILED,
                message=str(e),
            )

    
    def _exec_asana(self, action: PlannedAction) -> ExecutionResult:
        p = action.payload
        result = self.asana.create_task(
            name=p["name"],
            notes=p.get("notes", ""),
            project_gid=p.get("project_gid"),
            workspace_gid=p.get("workspace_gid"),
            assignee="nakulreddy0001@gmail.com",
            due_on=p.get("due_on"),
            priority=p.get("priority"),
        )
        status = ActionStatus.DRY_RUN if result.get("dry_run") else ActionStatus.EXECUTED
        return ExecutionResult(
            action_id=action.action_id,
            status=status,
            external_id=result.get("gid"),
            external_url=result.get("url"),
            message=f"Asana task {result.get('gid')} created",
            executed_at=datetime.utcnow(),
            raw_response=result,
        )

    def _exec_slack(self, action: PlannedAction) -> ExecutionResult:
        p = action.payload
        print("channel", p.get("channel"))
        result = self.slack.post_message(
            text=p["text"],
            channel="#testing",
            mentions=p.get("mentions"),
            urgency=p.get("urgency", "medium"),
        )
        status = ActionStatus.DRY_RUN if result.get("dry_run") else ActionStatus.EXECUTED
        return ExecutionResult(
            action_id=action.action_id,
            status=status,
            external_id=result.get("ts"),
            external_url=result.get("permalink"),
            message=f"Slack message posted to {result.get('channel')}",
            executed_at=datetime.utcnow(),
            raw_response=result,
        )

    def _exec_calendar(self, action: PlannedAction) -> ExecutionResult:
        p = action.payload
        result = self.calendar.create_event(
            title=p["title"],
            attendees=p.get("attendees", []),
            proposed_time=p.get("proposed_time"),
            duration_minutes=p.get("duration_minutes", 30),
            location_or_link=p.get("location_or_link"),
        )
        status = ActionStatus.DRY_RUN if result.get("dry_run") else ActionStatus.EXECUTED
        return ExecutionResult(
            action_id=action.action_id,
            status=status,
            external_id=result.get("event_id"),
            external_url=result.get("url"),
            message=f"Calendar event scheduled for {result.get('start')}",
            executed_at=datetime.utcnow(),
            raw_response=result,
        )

    def _exec_reply(self, action: PlannedAction) -> ExecutionResult:
        # By policy, email replies are ALWAYS drafts held for human approval.
        # The executor just marks this and stores the draft in the result.
        return ExecutionResult(
            action_id=action.action_id,
            status=ActionStatus.PENDING,
            message="Email reply drafted — awaiting human approval.",
            raw_response=action.payload,
        )
