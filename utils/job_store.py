"""Tiny file-based job store for background TTS jobs."""

import os
from typing import Optional

BASE_DIR = os.path.join(os.getcwd(), "tmp", "jobs")
os.makedirs(BASE_DIR, exist_ok=True)


def _path(job_id: str) -> str:
    safe = "".join(c for c in job_id if c.isalnum())
    return os.path.join(BASE_DIR, f"job_{safe}.txt")


def set_job_result(job_id: str, value: str) -> None:
    with open(_path(job_id), "w", encoding="utf-8") as f:
        f.write(value)


def get_job_result(job_id: str) -> Optional[str]:
    p = _path(job_id)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return None


def job_exists(job_id: str) -> bool:
    return os.path.exists(_path(job_id))

