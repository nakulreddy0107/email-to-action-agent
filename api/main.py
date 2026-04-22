"""FastAPI entry point.

Endpoints:
  GET  /                          — the SPA (HTML + JS)
  GET  /health                    — liveness
  GET  /api/samples               — sample emails for click-to-load buttons
  POST /api/emails/process        — run pipeline (returns full result)
  POST /api/emails/stream         — run pipeline with SSE live events
  GET  /api/runs                  — recent runs for history table (with ?q= search)
  GET  /api/runs/{email_id}       — full run detail
  GET  /api/actions/pending       — list actions awaiting human approval
  POST /api/actions/{id}/approve  — approve + execute a pending action
  POST /api/actions/{id}/reject   — reject a pending action
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.config import settings
from core.orchestrator import Orchestrator
from core.store import fetch_email_detail, fetch_recent_runs
from core.streaming import StreamingOrchestrator


app = FastAPI(
    title="Email-to-Action Agent",
    description="Multi-agent email automation using OpenAI + Jira + Slack",
    version="1.1.0",
)

_orchestrator: Orchestrator | None = None
_streamer: StreamingOrchestrator | None = None


def get_orchestrator() -> Orchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator()
    return _orchestrator


def get_streamer() -> StreamingOrchestrator:
    global _streamer
    if _streamer is None:
        _streamer = StreamingOrchestrator()
    return _streamer


# ----- Schemas -----

class EmailPayload(BaseModel):
    sender: str
    subject: str
    body: str
    recipients: list[str] = []
    message_id: str | None = None


# ----- Core endpoints -----

@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "env": settings.app_env,
        "model": settings.openai_model,
        "dry_run": settings.dry_run,
    }


@app.get("/api/samples")
def list_samples() -> dict[str, Any]:
    path = Path(__file__).parent.parent / "data" / "sample_emails.json"
    if not path.exists():
        return {"samples": []}
    with path.open() as f:
        samples = json.load(f)
    labeled = []
    for i, s in enumerate(samples):
        labeled.append({
            "id": i,
            "label": s.get("subject", f"Sample {i+1}")[:50],
            "sender": s.get("sender", ""),
            "subject": s.get("subject", ""),
            "body": s.get("body", ""),
        })
    return {"samples": labeled}


@app.post("/api/emails/process")
def process_email(payload: EmailPayload) -> dict[str, Any]:
    try:
        result = get_orchestrator().run(payload.model_dump(exclude_none=True))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {
        "email_id": result.email_id,
        "subject": result.subject,
        "intents": [i.model_dump() for i in result.intents],
        "actions": [a.model_dump() for a in result.actions],
        "results": [r.model_dump() for r in result.results],
        "audit_trail": result.audit_trail,
        "duration_ms": int((result.finished_at - result.started_at).total_seconds() * 1000),
    }


@app.post("/api/emails/stream")
def process_email_stream(payload: EmailPayload) -> StreamingResponse:
    """SSE endpoint — emits one event per pipeline stage as it runs."""
    streamer = get_streamer()

    def event_gen():
        try:
            for evt in streamer.stream(payload.model_dump(exclude_none=True)):
                yield f"data: {json.dumps(evt)}\n\n"
        except Exception as e:
            err = {"stage": "end", "agent": "orchestrator", "status": "error", "error": str(e)}
            yield f"data: {json.dumps(err)}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/runs")
def list_runs(limit: int = 50, q: str | None = None) -> dict[str, Any]:
    runs = fetch_recent_runs(limit=limit)
    if q:
        ql = q.lower()
        runs = [r for r in runs if ql in (r.get("subject") or "").lower() or ql in (r.get("sender") or "").lower()]
    return {"runs": runs}


@app.get("/api/runs/{email_id}")
def run_detail(email_id: str) -> dict[str, Any]:
    detail = fetch_email_detail(email_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Email not found")
    return detail


# ----- Human-in-the-loop endpoints -----

@app.get("/api/actions/pending")
def list_pending() -> dict[str, Any]:
    return {"pending": get_streamer().list_pending()}


@app.post("/api/actions/{action_id}/approve")
def approve_action(action_id: str) -> dict[str, Any]:
    try:
        result = get_streamer().approve(action_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {
        "status": result.status.value,
        "external_id": result.external_id,
        "external_url": result.external_url,
        "message": result.message,
    }


@app.post("/api/actions/{action_id}/reject")
def reject_action(action_id: str) -> dict[str, Any]:
    try:
        get_streamer().reject(action_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"status": "rejected"}


# ----- SPA serving -----

_STATIC_DIR = Path(__file__).parent / "static"
_STATIC_DIR.mkdir(exist_ok=True)

app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
def spa_root():
    index = _STATIC_DIR / "index.html"
    if not index.exists():
        return HTMLResponse(
            "<h1>UI not built</h1><p>api/static/index.html is missing.</p>",
            status_code=500,
        )
    return FileResponse(str(index))
