"""Voice mapping and per-user preference resolution."""

from __future__ import annotations

import os
import json
from typing import Dict, Optional

from utils.db import db_session
from utils.models import UserPreference


# Default voice keyword -> ElevenLabs voice ID mapping. Can be overridden with
# VOICE_MAP env var containing JSON like: {"sparkles":"<id>", "joanna":"<id>"}
DEFAULT_VOICE_MAP: Dict[str, str] = {
    "sparkles": os.environ.get("ELEVENLABS_VOICE_ID", os.environ.get("SPARKLES_VOICE_ID", "")),
}


def voice_map() -> Dict[str, str]:
    raw = os.environ.get("VOICE_MAP")
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return {str(k).lower(): str(v) for k, v in data.items()}
        except Exception:
            pass
    return {k.lower(): v for k, v in DEFAULT_VOICE_MAP.items() if v}


def list_voice_keywords() -> Dict[str, str]:
    return voice_map()


def get_user_voice_id(user_id: str) -> Optional[str]:
    with db_session() as s:
        pref = (
            s.query(UserPreference)
            .filter(UserPreference.user_id == user_id, UserPreference.key == "voice")
            .first()
        )
        if pref:
            # If value is a keyword, resolve it; if it's a direct voice id, return it
            m = voice_map()
            return m.get(pref.value.lower(), pref.value)
    # No user preference; fall back to default voice
    m = voice_map()
    # Return first available mapping if any
    return next(iter(m.values()), None)


def set_user_voice_keyword(user_id: str, keyword_or_id: str) -> str:
    """Set user's voice preference. Accepts keyword or raw voice id.

    Returns the resolved voice id actually stored (keyword preserved as value).
    """
    value = keyword_or_id.strip()
    with db_session() as s:
        pref = (
            s.query(UserPreference)
            .filter(UserPreference.user_id == user_id, UserPreference.key == "voice")
            .first()
        )
        if pref:
            pref.value = value
        else:
            pref = UserPreference(user_id=user_id, key="voice", value=value)
            s.add(pref)
    m = voice_map()
    return m.get(value.lower(), value)

