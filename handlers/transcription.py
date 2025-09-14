"""Speech-to-text helper using OpenAI Whisper."""

import os
from typing import Optional

import openai


openai.api_key = os.environ.get("OPENAI_API_KEY")


def transcribe_audio(audio_file: bytes, model: str = "whisper-1") -> Optional[str]:
    """Transcribe audio bytes using OpenAI Whisper."""
    if not openai.api_key:
        raise RuntimeError("OPENAI_API_KEY not set")
    try:
        transcript = openai.Audio.transcribe(model=model, file=audio_file)
        return transcript["text"]
    except Exception as exc:  # pragma: no cover - API call
        print(f"Transcription failed: {exc}")
        return None
