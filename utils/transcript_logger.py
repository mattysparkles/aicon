"""Utility for logging call transcripts."""

import json
import os
from datetime import datetime
from typing import Any, Dict


def log_transcript(caller: str, question: str, reply: str) -> str:
    """Persist a transcript entry to ``/transcripts``.

    Parameters
    ----------
    caller:
        Phone number of the caller.
    question:
        User's spoken input.
    reply:
        GPT-generated response.

    Returns
    -------
    str
        Path to the JSON transcript file.
    """

    transcript_dir = os.path.join(os.getcwd(), "transcripts")
    os.makedirs(transcript_dir, exist_ok=True)

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    data: Dict[str, Any] = {
        "timestamp": timestamp,
        "caller": caller,
        "question": question,
        "reply": reply,
    }

    safe_caller = (
        caller.replace("+", "")
        .replace(" ", "")
        .replace(":", "")
        .replace("/", "")
    )
    path = os.path.join(transcript_dir, f"{timestamp}_{safe_caller}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return path

