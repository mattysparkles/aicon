"""Text-to-speech utilities."""

import os
from typing import Optional

import openai

openai.api_key = os.environ.get("OPENAI_API_KEY")


def synthesize_speech(text: str, voice: str = "alloy", model: str = "gpt-4o-mini-tts") -> Optional[bytes]:
    """Convert text to speech using OpenAI TTS."""
    if not openai.api_key:
        raise RuntimeError("OPENAI_API_KEY not set")
    try:
        speech = openai.Audio.speech.create(
            model=model, voice=voice, input=text
        )
        return speech["data"]
    except Exception as exc:  # pragma: no cover - API call
        print(f"TTS failed: {exc}")
        return None
