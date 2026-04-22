"""Unit tests for the agent system.

These tests mock the LLM and the external HTTP calls so they run fully offline
and don't require OpenAI / Jira / Slack credentials.

Run with:
    pytest tests/
    # or
    python -m unittest tests/test_pipeline.py
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from agents.ingestion import IngestionAgent
from agents.policy import PolicyAgent
from core.models import (
    DetectedIntent,
    IntentType,
    PlannedAction,
)


class IngestionTests(unittest.TestCase):
    def test_strips_quoted_replies(self) -> None:
        agent = IngestionAgent()
        email = agent.ingest_from_dict({
            "sender": "Alice <alice@example.com>",
            "subject": "Re: hi",
            "body": "Sure, sounds good.\n\nOn Tue, Jan 1 2026, Bob wrote:\n> can you do X?",
        })
        self.assertEqual(email.sender, "alice@example.com")
        self.assertEqual(email.sender_name, "Alice")
        self.assertNotIn("Bob wrote", email.body)
        self.assertIn("sounds good", email.body)

    def test_strips_signature(self) -> None:
        agent = IngestionAgent()
        email = agent.ingest_from_dict({
            "sender": "alice@example.com",
            "subject": "hi",
            "body": "Hello there.\n\n-- \nAlice Smith\nCEO, ExampleCo",
        })
        self.assertNotIn("CEO", email.body)
        self.assertIn("Hello there", email.body)

    def test_assigns_stable_id(self) -> None:
        agent = IngestionAgent()
        a = agent.ingest_from_dict({"sender": "x@y.com", "subject": "s", "body": "b"})
        b = agent.ingest_from_dict({"sender": "x@y.com", "subject": "s", "body": "b"})
        self.assertEqual(a.message_id, b.message_id)


class PolicyTests(unittest.TestCase):
    def _make_action(self, tool: str = "jira", confidence: float = 0.9, **payload_overrides) -> PlannedAction:
        default_payloads = {
            "jira": {"summary": "Fix thing", "description": "..."},
            "slack": {"channel": "#general", "text": "hi"},
            "calendar": {"title": "M", "attendees": ["a@b.com"]},
            "email_reply": {"to": "a@b.com", "subject": "Re:", "body": "..."},
        }
        payload = {**default_payloads[tool], **payload_overrides}
        return PlannedAction(
            action_id="a1",
            intent_type=IntentType.CREATE_TASK,
            tool=tool,
            payload=payload,
            confidence=confidence,
            rationale="test",
        )

    def test_low_confidence_requires_human(self) -> None:
        policy = PolicyAgent()
        action = self._make_action(confidence=0.4)
        v = policy.evaluate(action)
        self.assertEqual(v.verdict, "require_human")

    def test_protected_channel_blocked(self) -> None:
        policy = PolicyAgent()
        action = self._make_action(tool="slack", channel="#executive-team", text="hi")
        v = policy.evaluate(action)
        self.assertEqual(v.verdict, "require_human")

    def test_email_reply_always_requires_human(self) -> None:
        policy = PolicyAgent()
        action = self._make_action(tool="email_reply", confidence=0.99)
        v = policy.evaluate(action)
        self.assertEqual(v.verdict, "require_human")

    def test_empty_jira_summary_rejected(self) -> None:
        policy = PolicyAgent()
        action = self._make_action(tool="jira", summary="")
        v = policy.evaluate(action)
        self.assertEqual(v.verdict, "reject")

    def test_happy_path_approved(self) -> None:
        policy = PolicyAgent()
        action = self._make_action(tool="jira", confidence=0.95)
        v = policy.evaluate(action)
        self.assertEqual(v.verdict, "approve")


class OrchestratorTests(unittest.TestCase):
    """End-to-end test with the LLM mocked out."""

    @patch("agents.intent_detection.chat_json")
    def test_multi_intent_email_flows_through(self, mock_chat) -> None:
        # Simulate the intent agent extracting two actions
        mock_chat.return_value = {
            "intents": [
                {
                    "intent_type": "create_task",
                    "summary": "Update onboarding docs",
                    "confidence": 0.9,
                    "entities": {
                        "title": "Update onboarding docs",
                        "priority": "high",
                        "assignee": "Jordan",
                    },
                    "raw_excerpt": "update the onboarding documentation",
                },
                {
                    "intent_type": "notify_team",
                    "summary": "Freeze deploys",
                    "confidence": 0.85,
                    "entities": {"channel": "#devops", "message": "Deploy freeze 3pm", "urgency": "high"},
                    "raw_excerpt": "give #devops a heads-up",
                },
            ],
            "overall_sentiment": "neutral",
            "reasoning": "Two distinct asks",
        }

        from core.orchestrator import Orchestrator
        orch = Orchestrator()
        result = orch.run({
            "sender": "pm@example.com",
            "subject": "Two things",
            "body": "Please update onboarding docs and ping #devops about freeze.",
        })

        self.assertEqual(len(result.intents), 2)
        self.assertEqual(len(result.actions), 2)
        tools = {a.tool for a in result.actions}
        self.assertEqual(tools, {"jira", "slack"})
        # In dry-run mode (default in tests), both should complete
        for r in result.results:
            self.assertIn(r.status.value, {"executed", "dry_run", "pending"})


class LLMCaptureTests(unittest.TestCase):
    """Verify the prompt-inspector capture mechanism."""

    def test_capture_records_prompt_and_response(self) -> None:
        from core.llm import start_capture, stop_capture, _capture_buf

        buf = start_capture()
        # Manually simulate what chat_json would do
        buf_from_ctx = _capture_buf.get()
        self.assertIs(buf, buf_from_ctx)
        buf_from_ctx.append({"model": "gpt-4o-mini", "tokens_in": 100})
        stop_capture()

        self.assertEqual(len(buf), 1)
        self.assertEqual(buf[0]["model"], "gpt-4o-mini")
        # After stop, context is cleared
        self.assertIsNone(_capture_buf.get())


if __name__ == "__main__":
    unittest.main()