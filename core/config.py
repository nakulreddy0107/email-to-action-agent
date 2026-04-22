"""Configuration loader. Reads from .env and exposes a typed Settings object."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    # OpenAI
    openai_api_key: str
    openai_model: str

    # Asana
    asana_access_token: str
    asana_default_project_gid: str
    asana_default_workspace_gid: str

    # Slack
    slack_bot_token: str
    slack_default_channel: str

    # Google Calendar
    google_calendar_enabled: bool = False
    google_calendar_credentials_path: str | None = None
  

    # App
    app_env: str = "development"
    database_url: str = "sqlite:///./data/agent.db"
    confidence_threshold: float = 0.70
    dry_run: bool = True


def _bool(val: str | None, default: bool = False) -> bool:
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_settings() -> Settings:
    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        asana_access_token=os.getenv("ASANA_ACCESS_TOKEN", ""),
        asana_default_project_gid=os.getenv("ASANA_DEFAULT_PROJECT_GID", ""),
        asana_default_workspace_gid=os.getenv("ASANA_DEFAULT_WORKSPACE_GID", ""),
        slack_bot_token=os.getenv("SLACK_BOT_TOKEN", ""),
        slack_default_channel=os.getenv("SLACK_DEFAULT_CHANNEL", "#general"),
        google_calendar_enabled=_bool(os.getenv("GOOGLE_CALENDAR_ENABLED"), False),
        google_calendar_credentials_path=os.getenv(
            "GOOGLE_CALENDAR_CREDENTIALS_PATH", "./credentials.json"
        ),
        app_env=os.getenv("APP_ENV", "development"),
        database_url=os.getenv("DATABASE_URL", "sqlite:///./data/agent.db"),
        confidence_threshold=float(os.getenv("CONFIDENCE_THRESHOLD", "0.70")),
        dry_run=_bool(os.getenv("DRY_RUN"), True),
    )


settings = load_settings()
