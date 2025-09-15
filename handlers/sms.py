"""Twilio SMS helper with batching and a simple sending queue."""

import os
from typing import Iterable, List, Optional, Tuple
import threading
import queue
import time
import logging

from twilio.rest import Client

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER")
logger = logging.getLogger(__name__)

client = (
    Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN
    else None
)


def _chunk_message(message: str, limit: int = 400) -> Iterable[str]:
    for i in range(0, len(message), limit):
        yield message[i : i + limit]


def send_sms(to: str, body: str, from_number: Optional[str] = None) -> None:
    """Send SMS via Twilio in safe chunks."""
    if not client or not TWILIO_PHONE_NUMBER:
        raise RuntimeError("Twilio client not configured")
    sender = from_number or TWILIO_PHONE_NUMBER
    for part in _chunk_message(body):
        logger.info("Sending SMS part len=%s to=%s from=%s", len(part), to, sender)
        client.messages.create(body=part, to=to, from_=sender)


# --- Simple background queue for multi-part sends ---
_q: "queue.Queue[Tuple[str, str, Optional[str]]]" = queue.Queue()
_worker_started = False
_lock = threading.Lock()


def _worker() -> None:
    while True:
        try:
            to, body, from_number = _q.get()
            if to is None:  # type: ignore
                break
            # Send with small delay between parts to avoid rate limiting
            for part in _chunk_message(body):
                send_sms(to, part, from_number=from_number)
                time.sleep(0.8)
        except Exception:
            time.sleep(1.0)
        finally:
            try:
                _q.task_done()
            except Exception:
                pass


def _ensure_worker() -> None:
    global _worker_started
    with _lock:
        if not _worker_started:
            t = threading.Thread(target=_worker, daemon=True)
            t.start()
            _worker_started = True


def enqueue_sms(to: str, body: str, from_number: Optional[str] = None) -> None:
    _ensure_worker()
    logger.info("Enqueue SMS len=%s to=%s from=%s", len(body), to, from_number or TWILIO_PHONE_NUMBER)
    _q.put((to, body, from_number))
