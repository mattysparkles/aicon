import os
import json
import time
from typing import Dict, Any

BASE_DIR = os.path.join(os.getcwd(), "tmp", "calls")
os.makedirs(BASE_DIR, exist_ok=True)


def _path(call_sid: str) -> str:
    safe = "".join(c for c in (call_sid or "") if c.isalnum())
    return os.path.join(BASE_DIR, f"call_{safe}.json")


def _default_state() -> Dict[str, Any]:
    now = time.time()
    return {
        "last_activity": now,
        "last_warning": 0,  # 0, 15, 10, 5
        "greeted": False,
        "verified": False,
    }


def get_state(call_sid: str) -> Dict[str, Any]:
    p = _path(call_sid)
    if not os.path.exists(p):
        return _default_state()
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Backfill keys if missing
            base = _default_state()
            base.update(data or {})
            return base
    except Exception:
        return _default_state()


def set_state(call_sid: str, state: Dict[str, Any]) -> None:
    p = _path(call_sid)
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception:
        pass


def touch_activity(call_sid: str) -> None:
    st = get_state(call_sid)
    st["last_activity"] = time.time()
    st["last_warning"] = 0
    set_state(call_sid, st)


def mark_greeted(call_sid: str) -> None:
    st = get_state(call_sid)
    st["greeted"] = True
    # Initialize activity at greet time
    st["last_activity"] = time.time()
    st["last_warning"] = 0
    set_state(call_sid, st)


def set_warning(call_sid: str, seconds: int) -> None:
    st = get_state(call_sid)
    st["last_warning"] = seconds
    set_state(call_sid, st)
