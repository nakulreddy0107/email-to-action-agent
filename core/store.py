"""SQLite-based audit logging.

Every email processed + every action taken is stored so we have a full trail for
compliance, debugging, and metrics.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from core.config import settings

# Derive file path from sqlite URL
_DB_PATH = settings.database_url.replace("sqlite:///", "")
Path(_DB_PATH).parent.mkdir(parents=True, exist_ok=True)


SCHEMA = """
CREATE TABLE IF NOT EXISTS emails (
    id TEXT PRIMARY KEY,
    sender TEXT,
    subject TEXT,
    body TEXT,
    received_at TEXT,
    processed_at TEXT
);

CREATE TABLE IF NOT EXISTS intents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id TEXT,
    intent_type TEXT,
    summary TEXT,
    confidence REAL,
    entities_json TEXT,
    created_at TEXT,
    FOREIGN KEY (email_id) REFERENCES emails(id)
);

CREATE TABLE IF NOT EXISTS actions (
    id TEXT PRIMARY KEY,
    email_id TEXT,
    tool TEXT,
    intent_type TEXT,
    payload_json TEXT,
    status TEXT,
    external_id TEXT,
    external_url TEXT,
    message TEXT,
    confidence REAL,
    created_at TEXT,
    executed_at TEXT,
    FOREIGN KEY (email_id) REFERENCES emails(id)
);
"""


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(_DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db() -> None:
    with _conn() as c:
        c.executescript(SCHEMA)


def log_email(email_id: str, sender: str, subject: str, body: str, received_at: datetime) -> None:
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO emails (id, sender, subject, body, received_at, processed_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (email_id, sender, subject, body, received_at.isoformat(), datetime.utcnow().isoformat()),
        )


def log_intent(email_id: str, intent_type: str, summary: str, confidence: float, entities: dict[str, Any]) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO intents (email_id, intent_type, summary, confidence, entities_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (email_id, intent_type, summary, confidence, json.dumps(entities), datetime.utcnow().isoformat()),
        )


def log_action(
    action_id: str,
    email_id: str,
    tool: str,
    intent_type: str,
    payload: dict[str, Any],
    status: str,
    confidence: float,
    external_id: str | None = None,
    external_url: str | None = None,
    message: str = "",
    executed_at: datetime | None = None,
) -> None:
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO actions "
            "(id, email_id, tool, intent_type, payload_json, status, external_id, external_url, message, confidence, created_at, executed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                action_id,
                email_id,
                tool,
                intent_type,
                json.dumps(payload),
                status,
                external_id,
                external_url,
                message,
                confidence,
                datetime.utcnow().isoformat(),
                executed_at.isoformat() if executed_at else None,
            ),
        )


def fetch_recent_runs(limit: int = 25) -> list[dict[str, Any]]:
    """Return recent emails with their action counts for the dashboard."""
    with _conn() as c:
        rows = c.execute(
            """
            SELECT e.id, e.sender, e.subject, e.received_at, e.processed_at,
                   (SELECT COUNT(*) FROM intents i WHERE i.email_id = e.id) AS intent_count,
                   (SELECT COUNT(*) FROM actions a WHERE a.email_id = e.id AND a.status IN ('executed', 'dry_run')) AS executed_count,
                   (SELECT COUNT(*) FROM actions a WHERE a.email_id = e.id) AS action_count
            FROM emails e
            ORDER BY e.processed_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def fetch_email_detail(email_id: str) -> dict[str, Any] | None:
    with _conn() as c:
        email = c.execute("SELECT * FROM emails WHERE id = ?", (email_id,)).fetchone()
        if not email:
            return None
        intents = c.execute(
            "SELECT * FROM intents WHERE email_id = ? ORDER BY id", (email_id,)
        ).fetchall()
        actions = c.execute(
            "SELECT * FROM actions WHERE email_id = ? ORDER BY created_at", (email_id,)
        ).fetchall()
        return {
            "email": dict(email),
            "intents": [dict(i) for i in intents],
            "actions": [dict(a) for a in actions],
        }
