"""OpenAI GPT interaction module (SDK v1.x compatible)."""

import os
import logging
from typing import List, Tuple

from openai import OpenAI
from openai import APIError  # type: ignore
from utils.db import db_session
from utils.models import Conversation, User
from utils import brand as brand_cfg

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


def _build_memory_context(user_id: int, latest_user_text: str) -> List[dict]:
    """Fetch recent conversation and optionally summarize as a system prompt.

    - Pull last 10 messages (user+ai) for this user_id.
    - If over ~3000 chars, summarize into a condensed context via a short model pass.
    - Return messages array suitable for Chat Completions.
    """
    history: List[Tuple[str, str]] = []
    with db_session() as s:
        rows = (
            s.query(Conversation)
            .filter(Conversation.user_id == user_id)
            .order_by(Conversation.created_at.desc())
            .limit(10)
            .all()
        )
        rows = list(reversed(rows))
        for r in rows:
            history.append((r.role, r.message))

    # Estimate size
    joined = "\n".join(f"{r}: {m}" for r, m in history)
    msgs: List[dict] = []
    # Brand-aware system prompt if configured
    try:
        sp = brand_cfg.system_prompt()
        if sp:
            msgs.append({"role": "system", "content": sp})
    except Exception:
        pass
    if len(joined) > 3000 and history:
        # Summarize history to keep context light
        summary_prompt = (
            "Summarize the following conversation between a user and an AI. "
            "Focus on goals, preferences, important facts, and unresolved items in 8-12 bullet points.\n\n" + joined
        )
        try:
            summary = chat_completion([{"role": "user", "content": summary_prompt}], model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))
            msgs.append({"role": "system", "content": f"Conversation summary for context:\n{summary}"})
        except Exception:
            # If summarization fails, proceed without it
            pass
    else:
        if history:
            msgs.append({"role": "system", "content": "Short recent context follows. Use it only if relevant."})
    # Append last 5-10 raw turns
    for role, content in history[-10:]:
        rr = "assistant" if role == "ai" else "user"
        msgs.append({"role": rr, "content": content})
    # Finally the current user input
    msgs.append({"role": "user", "content": latest_user_text})
    return msgs


def get_gpt_response_with_memory(phone: str, user_text: str) -> str:
    """Reply using recent memory if enabled for the user with this phone.

    Falls back to a single-turn response if user not found or memory disabled.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")
    client = OpenAI(api_key=api_key)

    user_id = None
    memory_on = False
    with db_session() as s:
        u = s.query(User).filter(User.phone == phone).first()
        if u:
            user_id = u.id
            memory_on = (getattr(u, "memory_enabled", "true") or "true").lower() == "true"
    try:
        if user_id and memory_on:
            messages = _build_memory_context(user_id, user_text)
        else:
            # No memory: still prepend brand system prompt if available
            messages = []
            sp = brand_cfg.system_prompt()
            if sp:
                messages.append({"role": "system", "content": sp})
            messages.append({"role": "user", "content": user_text})
        response = client.chat.completions.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            messages=messages,
        )
        return (response.choices[0].message.content or "").strip()
    except APIError as exc:
        logger.error("OpenAI API error: %s", exc)
        raise
    except Exception:
        logger.exception("Unexpected error when calling OpenAI API")
        raise
