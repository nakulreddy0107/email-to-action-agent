"""Asana integration client.

Uses Asana's REST API (https://app.asana.com/api/1.0/tasks).
Supports dry-run mode (logs instead of calling) so the system is fully testable
without real credentials.

Auth: Personal Access Token (PAT). Create one at:
  https://app.asana.com/0/my-apps

Docs: https://developers.asana.com/reference/createtask
"""
from __future__ import annotations

import logging
from typing import Any

import requests

from core.config import settings

logger = logging.getLogger(__name__)


class AsanaClient:
    API_BASE = "https://app.asana.com/api/1.0"

    def __init__(self, dry_run: bool | None = None) -> None:
        self.token = settings.asana_access_token
        self.default_project_gid = settings.asana_default_project_gid
        self.default_workspace_gid = settings.asana_default_workspace_gid
        self.dry_run = settings.dry_run if dry_run is None else dry_run

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _is_configured(self) -> bool:
        return bool(self.token) and bool(self.default_project_gid or self.default_workspace_gid)

    def create_task(
        self,
        name: str,
        notes: str = "",
        project_gid: str | None = None,
        workspace_gid: str | None = None,
        assignee: str | None = None,
        due_on: str | None = None,
        priority: str | None = None,
    ) -> dict[str, Any]:
        """Create an Asana task.

        - name:         task title (required)
        - notes:        plain-text description
        - project_gid:  numeric project id — task will be added to this project
        - workspace_gid:required if no project_gid given
        - assignee:     user gid OR the string "me" OR an email
        - due_on:       "YYYY-MM-DD"
        - priority:     mapped to a tag in the notes since Asana doesn't have a
                        native priority field (it varies per workspace)
        """
        project_gid = project_gid or self.default_project_gid
        workspace_gid = workspace_gid or self.default_workspace_gid

        # Priority is not native in Asana — prepend to notes so it's visible
        if priority:
            notes = f"[Priority: {priority.upper()}]\n\n{notes}".strip()

        data: dict[str, Any] = {"name": name, "notes": notes}
        if project_gid:
            data["projects"] = [project_gid]
        if workspace_gid and not project_gid:
            # Workspace is only required when no project is supplied
            data["workspace"] = workspace_gid
        if assignee:
            data["assignee"] = assignee
        if due_on:
            data["due_on"] = due_on

        payload = {"data": data}

        if self.dry_run or not self._is_configured():
            logger.info("[DRY RUN] Asana create_task: %s", name)
            fake_gid = f"dry{abs(hash(name)) % 10_000_000}"
            return {
                "dry_run": True,
                "gid": fake_gid,
                "url": f"https://app.asana.com/0/{project_gid or 'dry'}/{fake_gid}",
                "payload": payload,
            }

        resp = requests.post(
            f"{self.API_BASE}/tasks",
            headers=self._headers(),
            json=payload,
            timeout=15,
        )
        if resp.status_code >= 400:
            try:
                err = resp.json()
            except Exception:
                err = {"body": resp.text}
            raise RuntimeError(
                f"Asana {resp.status_code} error creating task:\n"
                f"  errors: {err.get('errors', err)}\n"
                f"  payload sent: {payload}"
            )
        data = resp.json().get("data", {})
        gid = data.get("gid")
        return {
            "dry_run": False,
            "gid": gid,
            "url": data.get("permalink_url") or f"https://app.asana.com/0/{project_gid}/{gid}",
            "raw": data,
        }