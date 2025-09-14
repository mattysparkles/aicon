"""OpenAI GPT interaction module."""

import os
import logging
from typing import List

import openai

openai.api_key = os.environ.get("OPENAI_API_KEY")
logger = logging.getLogger(__name__)


def chat_completion(messages: List[dict], model: str = "gpt-4o-mini") -> str:
    """Send messages to OpenAI ChatCompletion."""
    if not openai.api_key:
        raise RuntimeError("OPENAI_API_KEY not set")
    resp = openai.ChatCompletion.create(model=model, messages=messages)
    return resp["choices"][0]["message"]["content"]


def get_gpt_response(prompt: str) -> str:
    """Return a GPT reply for a given prompt using OpenAI's Chat Completion API.

    Parameters
    ----------
    prompt:
        The text prompt to send to the model.

    Returns
    -------
    str
        The assistant's textual reply.
    """

    if not openai.api_key:
        raise RuntimeError("OPENAI_API_KEY not set")

    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
        )
    except openai.error.OpenAIError as exc:
        logger.error("OpenAI API error: %s", exc)
        raise
    except Exception as exc:  # pragma: no cover - unexpected errors
        logger.exception("Unexpected error when calling OpenAI API")
        raise

    return response["choices"][0]["message"]["content"].strip()
