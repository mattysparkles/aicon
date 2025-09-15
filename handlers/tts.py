"""Text-to-speech utilities."""

import os
import uuid
import hashlib
from typing import Optional
import logging

from openai import OpenAI
import requests
import time

logger = logging.getLogger(__name__)


def synthesize_speech(text: str, voice: str = "alloy", model: str = "gpt-4o-mini-tts") -> Optional[bytes]:
    """Convert text to speech using OpenAI TTS (SDK v1.x)."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")
    client = OpenAI(api_key=api_key)
    try:
        speech = client.audio.speech.create(model=model, voice=voice, input=text)
        # In SDK v1.x, the response provides a streaming reader; `.read()` returns bytes
        return speech.read()
    except Exception as exc:  # pragma: no cover - API call
        print(f"TTS failed: {exc}")
        return None


def _output_dir() -> str:
    tts_dir = os.environ.get("TTS_AUDIO_DIR", "static/audio")
    if not os.path.isabs(tts_dir):
        output_dir = os.path.join(os.getcwd(), tts_dir)
    else:
        output_dir = tts_dir
    os.makedirs(output_dir, exist_ok=True)
    return output_dir


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

    # Prefer the explicitly named ElevenLabs voice id, fall back to the older
    # SPARKLES_VOICE_ID env var for backwards compatibility.
    voice_id = os.environ.get(
        "ELEVENLABS_VOICE_ID", os.environ.get("SPARKLES_VOICE_ID", "<sparkles-voice-id>")
    )
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
    }
    output_dir = _output_dir()

    # Use deterministic filename to enable caching per voice+text
    cache_key = hashlib.sha1(f"{voice_id}:{text}".encode("utf-8")).hexdigest()
    filename = f"sparkles_{cache_key}.mp3"
    file_path = os.path.join(output_dir, filename)

    # If already rendered, return existing file path immediately
    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
        # Compute public relative path rooted at /static
        rel = os.path.relpath(file_path, os.getcwd()).replace("\\", "/")
        return rel

    # Render via ElevenLabs with a reasonable timeout to avoid webhook delays
    try:
        response = requests.post(
            url, headers=headers, json={"text": text}, timeout=14
        )
        response.raise_for_status()
    except requests.Timeout:
        logger.error("ElevenLabs TTS timeout for voice_id=%s", voice_id)
        raise
    except requests.HTTPError as http_err:
        body = getattr(http_err.response, "text", "") if hasattr(http_err, "response") else ""
        logger.error(
            "ElevenLabs HTTP error: %s | status=%s | body=%s",
            http_err, getattr(http_err.response, "status_code", "?"), body[:500]
        )
        raise
    except requests.RequestException as req_err:
        logger.error("ElevenLabs request error: %s", req_err)
        raise

    with open(file_path, "wb") as f:
        f.write(response.content)

    rel = os.path.relpath(file_path, os.getcwd()).replace("\\", "/")
    return rel


def generate_elevenlabs_voice(text: str, voice_id: str, attempts: int = 2, backoff: float = 0.6) -> str:
    """Generalized ElevenLabs TTS using a provided voice id."""
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY not set")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
    }

    output_dir = _output_dir()
    cache_key = hashlib.sha1(f"{voice_id}:{text}".encode("utf-8")).hexdigest()
    filename = f"sparkles_{cache_key}.mp3"
    file_path = os.path.join(output_dir, filename)

    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
        return os.path.relpath(file_path, os.getcwd()).replace("\\", "/")

    last_exc: Exception | None = None
    for i in range(max(1, attempts)):
        try:
            response = requests.post(url, headers=headers, json={"text": text}, timeout=14)
            response.raise_for_status()
            with open(file_path, "wb") as f:
                f.write(response.content)
            return os.path.relpath(file_path, os.getcwd()).replace("\\", "/")
        except requests.RequestException as exc:
            last_exc = exc
            logger.error("ElevenLabs TTS failed (attempt %s/%s): %s", i + 1, attempts, exc)
            try:
                from .metrics import tts_failures
                tts_failures.inc()
            except Exception:
                pass
            # For 401/403, retrying likely won't help, but allow 1 quick retry in case of transient key read
            if i < attempts - 1:
                time.sleep(backoff * (i + 1))
            else:
                break

    assert last_exc is not None
    raise last_exc
