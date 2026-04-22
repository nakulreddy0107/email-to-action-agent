"""Reply Drafter Agent.

Generates a draft email response using the LLM. The output is a PlannedAction
(tool="email_reply") — we never auto-send replies; the draft is stored for human
review.
"""
from __future__ import annotations

import uuid

from core.llm import chat_json
from core.models import DetectedIntent, EmailMessage, PlannedAction, IntentType


_SYSTEM_PROMPT = """You are an executive assistant drafting email replies on
behalf of the recipient. Output STRICT JSON:
{
  "subject": "<reply subject, usually 'Re: ...'>",
  "body": "<plain-text reply, 3-6 sentences, polite and concrete>",
  "tone_used": "formal|casual|apologetic"
}

Guidelines:
- Acknowledge the requests the sender made.
- If meetings/tasks are being scheduled automatically, mention it.
- Do not promise things outside the listed actions.
- Use the requested tone.
- No markdown, no fences."""


class ReplyAgent:
    name = "reply"

    def plan(
        self,
        intent: DetectedIntent,
        email: EmailMessage,
        planned_actions_summary: list[str],
    ) -> PlannedAction:
        tone = intent.entities.get("tone", "formal")
        key_points = intent.entities.get("key_points") or []

        user_prompt = (
            f"Original email from {email.sender_name or email.sender}:\n"
            f"Subject: {email.subject}\n"
            f"---\n{email.body}\n---\n\n"
            f"Desired tone: {tone}\n"
            f"Key points to cover: {key_points}\n"
            f"Actions already being taken automatically on this request:\n"
            + "\n".join(f"- {s}" for s in planned_actions_summary)
            + "\n\nDraft the reply now as valid JSON."
        )

        raw = chat_json(_SYSTEM_PROMPT, user_prompt, temperature=0.3)

        return PlannedAction(
            action_id=str(uuid.uuid4()),
            intent_type=IntentType.SEND_REPLY,
            tool="email_reply",
            payload={
                "to": email.sender,
                "subject": raw.get("subject", f"Re: {email.subject}"),
                "body": raw.get("body", ""),
                "tone_used": raw.get("tone_used", tone),
            },
            confidence=intent.confidence,
            requires_approval=True,  # email replies ALWAYS require approval
            rationale="Drafted reply — held for human approval before sending.",
        )
