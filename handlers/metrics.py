from __future__ import annotations

from flask import Blueprint, Response
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST

bp = Blueprint("metrics", __name__)

sms_requests = Counter("aicon_sms_requests_total", "Incoming SMS requests")
voice_requests = Counter("aicon_voice_requests_total", "Incoming voice requests")
sms_replies = Counter("aicon_sms_replies_total", "SMS replies sent")
onboarding_starts = Counter("aicon_onboarding_starts_total", "Onboarding starts")
payments_started = Counter("aicon_payments_started_total", "Payments started")
tts_failures = Counter("aicon_tts_failures_total", "TTS failures")


@bp.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)

