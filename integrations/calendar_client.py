from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from core.config import settings

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]


class CalendarClient:
    def __init__(self, dry_run: bool | None = None) -> None:
        self.enabled = settings.google_calendar_enabled
        self.credentials_path = settings.google_calendar_credentials_path
        self.token_path = "./token.json"
        self.dry_run = settings.dry_run if dry_run is None else dry_run

    def _is_configured(self) -> bool:
        return self.enabled and bool(self.credentials_path)

    def _get_service(self):
        creds = None

        if os.path.exists(self.token_path):
            creds = Credentials.from_authorized_user_file(self.token_path, SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_path,
                    SCOPES,
                )
                creds = flow.run_local_server(port=0)

            with open(self.token_path, "w") as token:
                token.write(creds.to_json())

        return build("calendar", "v3", credentials=creds)

    def create_event(
        self,
        title: str,
        attendees: list[str],
        proposed_time: str | None,
        duration_minutes: int = 30,
        location_or_link: str | None = None,
    ) -> dict[str, Any]:
        start = self._resolve_start(proposed_time)
        end = start + timedelta(minutes=duration_minutes)

        payload = {
            "summary": title,
            "start": {
                "dateTime": start.isoformat(),
                "timeZone": "America/Chicago",
            },
            "end": {
                "dateTime": end.isoformat(),
                "timeZone": "America/Chicago",
            },
            "attendees": [{"email": a} for a in attendees if "@" in a],
        }

        if location_or_link:
            payload["location"] = location_or_link

        if self.dry_run or not self._is_configured():
            logger.info("[DRY RUN] Calendar create_event: %s at %s", title, start)
            fake_id = f"evt_{abs(hash(title + start.isoformat())) % 10_000_000}"
            return {
                "dry_run": True,
                "event_id": fake_id,
                "url": f"https://calendar.google.com/event?eid={fake_id}",
                "start": start.isoformat(),
                "end": end.isoformat(),
                "payload": payload,
            }

        service = self._get_service()

        created = service.events().insert(
            calendarId="primary",
            body=payload,
            sendUpdates="all",
        ).execute()

        return {
            "dry_run": False,
            "event_id": created.get("id"),
            "url": created.get("htmlLink"),
            "start": created["start"].get("dateTime"),
            "end": created["end"].get("dateTime"),
            "raw": created,
        }

    @staticmethod
    def _resolve_start(proposed: str | None) -> datetime:
        if proposed:
            try:
                return datetime.fromisoformat(proposed.replace("Z", "+00:00"))
            except ValueError:
                pass

        tomorrow = (
            datetime.utcnow().replace(hour=10, minute=0, second=0, microsecond=0)
            + timedelta(days=1)
        )
        return tomorrow