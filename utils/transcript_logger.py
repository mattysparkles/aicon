"""Utility for persisting call transcripts to JSON files."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict


def log_transcript(call_sid: str, from_number: str, to_number: str, user_text: str, gpt_reply: str) -> str:
    """Persist a transcript entry to ``transcripts/``.

    Parameters
    ----------
    call_sid:
        Identifier for the call from Twilio.
    from_number:
        Caller phone number.
    to_number:
        Callee phone number.
    user_text:
        User's transcribed speech.
    gpt_reply:
        Response from GPT model.

    Returns
    -------
    str
        Path to the JSON transcript file.
    """

    transcript_dir = os.path.join(os.getcwd(), "transcripts")
    os.makedirs(transcript_dir, exist_ok=True)

    now = datetime.utcnow()
    timestamp = now.isoformat()
    file_ts = now.strftime("%Y%m%d_%H%M%S")
    data: Dict[str, Any] = {
        "timestamp": timestamp,
        "call_sid": call_sid,
        "from": from_number,
        "to": to_number,
        "user_text": user_text,
        "gpt_reply": gpt_reply,
    }

    safe_sid = "".join(ch for ch in call_sid if ch.isalnum())
    filename = f"call_{file_ts}_{safe_sid}.json"
    path = os.path.join(transcript_dir, filename)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return path
