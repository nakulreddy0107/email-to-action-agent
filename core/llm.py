"""Thin wrapper around the OpenAI client.

All agents go through this module so we centralize:
- API key handling
- JSON-mode enforcement
- Retry + error handling
- Token / cost logging
- Optional capture of prompt + response for the UI's prompt inspector
"""
from __future__ import annotations

import contextvars
import json
import logging
import time
from typing import Any, Type, TypeVar

from openai import OpenAI, OpenAIError
from pydantic import BaseModel, ValidationError

from core.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_client: OpenAI | None = None

# Per-request capture buffer. When a request wants to inspect LLM calls, it
# sets this to a list; every chat_json call appends a record to it.
_capture_buf: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar(
    "llm_capture_buf", default=None
)


def start_capture() -> list[dict[str, Any]]:
    """Start capturing LLM calls in the current context. Returns the buffer list."""
    buf: list[dict[str, Any]] = []
    _capture_buf.set(buf)
    return buf


def stop_capture() -> None:
    _capture_buf.set(None)


def get_client() -> OpenAI:
    global _client
    if _client is None:
        if not settings.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Copy .env.example to .env and fill it in."
            )
        _client = OpenAI(api_key=settings.openai_api_key)
    return _client


def chat_json(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str | None = None,
    temperature: float = 0.1,
    max_retries: int = 2,
) -> dict[str, Any]:
    """Call OpenAI with JSON-mode and return parsed dict.

    Uses response_format={"type": "json_object"} so we always get valid JSON back.
    """
    client = get_client()
    model = model or settings.openai_model

    last_err: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            t0 = time.time()
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                response_format={"type": "json_object"},
            )
            elapsed_ms = int((time.time() - t0) * 1000)
            content = resp.choices[0].message.content or "{}"
            usage = resp.usage
            logger.info(
                "llm_call model=%s tokens_in=%s tokens_out=%s ms=%s",
                model,
                usage.prompt_tokens if usage else "?",
                usage.completion_tokens if usage else "?",
                elapsed_ms,
            )
            parsed = json.loads(content)

            # If capture is active in this context, record the call
            buf = _capture_buf.get()
            if buf is not None:
                buf.append({
                    "model": model,
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                    "response": parsed,
                    "raw_response": content,
                    "tokens_in": usage.prompt_tokens if usage else None,
                    "tokens_out": usage.completion_tokens if usage else None,
                    "elapsed_ms": elapsed_ms,
                })

            return parsed
        except (OpenAIError, json.JSONDecodeError) as e:
            last_err = e
            logger.warning("LLM call failed (attempt %d): %s", attempt + 1, e)
            time.sleep(0.5 * (attempt + 1))

    raise RuntimeError(f"LLM call failed after {max_retries + 1} attempts: {last_err}")


def chat_structured(
    system_prompt: str,
    user_prompt: str,
    schema: Type[T],
    *,
    model: str | None = None,
    temperature: float = 0.1,
) -> T:
    """Like chat_json but validates against a Pydantic schema."""
    raw = chat_json(system_prompt, user_prompt, model=model, temperature=temperature)
    try:
        return schema.model_validate(raw)
    except ValidationError as e:
        # Single retry asking the model to fix its output to match the schema
        repair_prompt = (
            f"Your previous response did not match the expected schema.\n"
            f"Validation errors:\n{e}\n\n"
            f"Previous response:\n{json.dumps(raw, indent=2)}\n\n"
            f"Please re-output a valid JSON object matching the required schema."
        )
        raw2 = chat_json(system_prompt, repair_prompt, model=model, temperature=0.0)
        return schema.model_validate(raw2)
