"""OpenAI GPT interaction module."""

import os
from typing import List

import openai

openai.api_key = os.environ.get("OPENAI_API_KEY")


def chat_completion(messages: List[dict], model: str = "gpt-4o-mini") -> str:
    """Send messages to OpenAI ChatCompletion."""
    if not openai.api_key:
        raise RuntimeError("OPENAI_API_KEY not set")
    resp = openai.ChatCompletion.create(model=model, messages=messages)
    return resp["choices"][0]["message"]["content"]
