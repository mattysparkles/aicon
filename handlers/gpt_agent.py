"""OpenAI GPT interaction module (SDK v1.x compatible)."""

import os
import logging
from typing import List

from openai import OpenAI
from openai import APIError  # type: ignore

logger = logging.getLogger(__name__)


def chat_completion(messages: List[dict], model: str = "gpt-4o-mini") -> str:
    """Send messages to OpenAI using the Chat Completions API."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(model=model, messages=messages)
    return resp.choices[0].message.content or ""


def get_gpt_response(prompt: str) -> str:
    """Return a GPT reply for a given prompt using OpenAI's Chat Completions API."""

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")
    client = OpenAI(api_key=api_key)

    try:
        response = client.chat.completions.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
        )
    except APIError as exc:  # pragma: no cover - external service error
        logger.error("OpenAI API error: %s", exc)
        raise
    except Exception:  # pragma: no cover - unexpected errors
        logger.exception("Unexpected error when calling OpenAI API")
        raise

    return (response.choices[0].message.content or "").strip()
