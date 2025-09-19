import json
import os
from typing import List, Optional

_CACHE = None


def _brand_path() -> str:
    # Allow override via env var; default to config/brand.json in CWD
    p = os.environ.get("BRAND_CONFIG_PATH", os.path.join(os.getcwd(), "config", "brand.json"))
    return p


def _load() -> dict:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    p = _brand_path()
    try:
        with open(p, "r", encoding="utf-8") as f:
            _CACHE = json.load(f) or {}
    except Exception:
        _CACHE = {}
    return _CACHE


def name(default: str = "AICon") -> str:
    return str(_load().get("name", default))


def assistant_name(default: str = "Sparkles") -> str:
    return str(_load().get("assistant_name", default))


def system_prompt() -> Optional[str]:
    sp = _load().get("system_prompt")
    return str(sp) if isinstance(sp, str) and sp.strip() else None


def sms_help_lines() -> List[str]:
    lines = _load().get("sms_help")
    if isinstance(lines, list) and lines:
        return [str(x) for x in lines]
    return [
        "AICon SMS Commands:",
        "- help: show this menu",
        "- signup | onboard | sign up: start onboarding",
        "- pay [plan] [crypto]: get a secure payment link",
        "- memory on|off: enable or disable memory",
        "- pause on|off: pause or resume usage",
        "- voice list: list available voices",
        "- voice <keyword>: set preferred voice (e.g., sparkles)",
        "- set pass <phrase>: set your security phrase (SMS)",
        "- verify pass <phrase>: verify with your security phrase",
    ]

