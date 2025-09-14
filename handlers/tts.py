"""Text-to-speech utilities."""

import os
import uuid
from typing import Optional

import openai
import requests

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


def generate_sparkles_voice(text: str) -> str:
    """Generate speech using ElevenLabs' Sparkles voice.

    The function sends ``text`` to the ElevenLabs text-to-speech API using the
    Sparkles voice and saves the resulting ``.mp3`` file into the application's
    ``static`` directory. A public-facing path to the audio file is returned.

    Parameters
    ----------
    text:
        Text to synthesize.

    Returns
    -------
    str
        Relative path to the saved ``.mp3`` file suitable for serving via
        Flask's static file handler.
    """

    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY not set")

    # TODO: replace with the actual Sparkles voice ID.
    voice_id = os.environ.get("SPARKLES_VOICE_ID", "<sparkles-voice-id>")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
    }
    response = requests.post(url, headers=headers, json={"text": text})
    response.raise_for_status()

    filename = f"sparkles_{uuid.uuid4().hex}.mp3"
    static_dir = os.path.join(os.getcwd(), "static")
    os.makedirs(static_dir, exist_ok=True)
    file_path = os.path.join(static_dir, filename)
    with open(file_path, "wb") as f:
        f.write(response.content)

    return os.path.join("static", filename)
