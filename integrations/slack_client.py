"""Slack integration client.

Uses Slack Web API (chat.postMessage). Supports dry-run mode.

Docs: https://api.slack.com/methods/chat.postMessage
"""
from __future__ import annotations

import logging
from typing import Any

import requests

from core.config import settings

logger = logging.getLogger(__name__)


class SlackClient:
    def __init__(self, dry_run: bool | None = None) -> None:
        self.token = settings.slack_bot_token
        self.default_channel = settings.slack_default_channel
        self.dry_run = settings.dry_run if dry_run is None else dry_run

    def _is_configured(self) -> bool:
        return bool(self.token)

    def post_message(
        self,
        text: str,
        channel: str | None = None,
        mentions: list[str] | None = None,
        urgency: str = "medium",
    ) -> dict[str, Any]:
        channel = channel or self.default_channel

        # Prepend mentions and urgency emoji
        emoji = {"low": "", "medium": "📣", "high": "🚨"}.get(urgency, "📣")
        mention_str = " ".join(f"<@{m.lstrip('@')}>" for m in (mentions or []))
        body = f"{emoji} {mention_str} {text}".strip()

        payload = {"channel": channel, "text": body}

        if self.dry_run or not self._is_configured():
            logger.info("[DRY RUN] Slack post to %s: %s", channel, body[:80])
            return {
                "dry_run": True,
                "channel": channel,
                "ts": "0000000000.000000",
                "permalink": f"https://slack.com/archives/DRYRUN",
                "payload": payload,
            }

        resp = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json=payload,
            timeout=10,
        )
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Slack API error: {data.get('error')}")
        return {
            "dry_run": False,
            "channel": data.get("channel"),
            "ts": data.get("ts"),
            "permalink": f"https://slack.com/archives/{data.get('channel')}/p{str(data.get('ts', '')).replace('.', '')}",
            "raw": data,
        }
