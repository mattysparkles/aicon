"""Twilio SMS helper with batching."""

import os
from typing import Iterable

from twilio.rest import Client

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER")

client = (
    Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN
    else None
)


def _chunk_message(message: str, limit: int = 1600) -> Iterable[str]:
    for i in range(0, len(message), limit):
        yield message[i : i + limit]


def send_sms(to: str, body: str) -> None:
    """Send SMS via Twilio in safe chunks."""
    if not client or not TWILIO_PHONE_NUMBER:
        raise RuntimeError("Twilio client not configured")
    for part in _chunk_message(body):
        client.messages.create(body=part, to=to, from_=TWILIO_PHONE_NUMBER)
