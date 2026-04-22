"""Intent Detection + Task Decomposition Agent.

Takes a cleaned EmailMessage and returns an IntentAnalysis with ALL the atomic
requests found in the email. One email → many intents. This is where the LLM
earns its keep.
"""
from __future__ import annotations

import json

from core.llm import chat_json
from core.models import (
    DetectedIntent,
    EmailMessage,
    IntentAnalysis,
    IntentType,
)


_SYSTEM_PROMPT = """You are an enterprise email triage agent. Your job is to read
an email and extract EVERY distinct actionable request it contains.

You must return STRICT JSON matching this schema:
{
  "intents": [
    {
      "intent_type": "schedule_meeting" | "create_task" | "notify_team" | "send_reply" | "informational" | "unknown",
      "summary": "<one-line description of the action>",
      "confidence": <float 0.0 to 1.0>,
      "entities": { /* action-specific fields, see below */ },
      "raw_excerpt": "<the sentence from the email that triggered this intent>"
    }
  ],
  "overall_sentiment": "neutral" | "positive" | "urgent" | "negative",
  "reasoning": "<brief explanation of how you decomposed the email>"
}

Entity guidance by intent_type:
- schedule_meeting: { "title": str, "attendees": [emails or names], "proposed_time": str ISO-ish or natural, "duration_minutes": int, "location_or_link": str }
- create_task: { "title": str, "description": str, "assignee": str, "priority": "low|medium|high", "due_date": str, "project": str }
- notify_team: { "channel": str (e.g. "#devops"), "message": str, "urgency": "low|medium|high", "mentions": [str] }
- send_reply: { "tone": "formal|casual|apologetic", "key_points": [str] }
- informational: { "topic": str }

Rules:
1. If an email contains multiple requests, emit MULTIPLE intents — one per atomic action.
2. Set confidence LOW (<0.6) when the email is ambiguous, missing critical info, or you're guessing.
3. Set confidence HIGH (>0.85) only when the request is explicit and all required entities are present.
4. Do NOT invent email addresses or names not mentioned in the text.
5. If the email is purely informational (FYI, status update with no ask), emit one "informational" intent.
6. Return ONLY the JSON object. No prose, no markdown fences."""


class IntentDetectionAgent:
    name = "intent_detection"

    def analyze(self, email: EmailMessage) -> IntentAnalysis:
        user_prompt = (
            f"FROM: {email.sender_name or ''} <{email.sender}>\n"
            f"TO: {', '.join(email.recipients) or '(unspecified)'}\n"
            f"SUBJECT: {email.subject}\n"
            f"RECEIVED: {email.received_at.isoformat()}\n"
            f"---\n{email.body}\n---\n\n"
            f"Extract all atomic intents. Return ONLY valid JSON."
        )

        raw = chat_json(_SYSTEM_PROMPT, user_prompt, temperature=0.1)

        # Validate + coerce into our pydantic models.
        intents_raw = raw.get("intents", [])
        intents: list[DetectedIntent] = []
        for item in intents_raw:
            try:
                intent_type = IntentType(item.get("intent_type", "unknown"))
            except ValueError:
                intent_type = IntentType.UNKNOWN
            intents.append(
                DetectedIntent(
                    intent_type=intent_type,
                    summary=item.get("summary", ""),
                    confidence=float(item.get("confidence", 0.0)),
                    entities=item.get("entities", {}) or {},
                    raw_excerpt=item.get("raw_excerpt"),
                )
            )

        # Fallback: if the LLM returned nothing, mark as unknown with low confidence
        if not intents:
            intents = [
                DetectedIntent(
                    intent_type=IntentType.UNKNOWN,
                    summary="No clear intent detected",
                    confidence=0.2,
                    entities={},
                )
            ]

        return IntentAnalysis(
            email_id=email.message_id,
            intents=intents,
            overall_sentiment=raw.get("overall_sentiment", "neutral"),
            reasoning=raw.get("reasoning"),
        )
