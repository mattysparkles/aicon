"""Flask routes for handling Twilio voice calls."""

import os
import requests
from flask import Request, Response, current_app, render_template, request
from twilio.twiml.voice_response import VoiceResponse

from . import email, gpt_agent, sms, ssh, transcription, tts


def init_app(app):
    @app.route("/voice", methods=["GET", "POST"])
    def voice() -> Response:
        """Initial Twilio webhook for incoming calls."""
        vr = VoiceResponse()
        vr.say("Welcome to the AI assistant. Please speak after the tone.")
        vr.record(action="/transcribe", play_beep=True, max_length=60)
        return Response(str(vr), mimetype="text/xml")

    @app.route("/transcribe", methods=["POST"])
    def transcribe() -> Response:
        """Handle recording callback, transcribe and respond."""
        recording_url = request.form.get("RecordingUrl")
        audio_content = requests.get(f"{recording_url}.wav").content
        text = transcription.transcribe_audio(audio_content) or "I did not catch that."
        reply = gpt_agent.chat_completion([
            {"role": "system", "content": "You are a helpful voice assistant."},
            {"role": "user", "content": text},
        ])

        # Example side actions triggered by keywords in the reply
        lower = reply.lower()
        if lower.startswith("sms:"):
            sms.send_sms(os.environ.get("TWILIO_TEST_NUMBER", ""), reply[4:].strip())
        elif lower.startswith("email:"):
            email.send_email(
                os.environ.get("TEST_EMAIL", ""),
                "AI Assistant Message",
                reply[6:].strip(),
            )
        elif lower.startswith("ssh:"):
            ssh.execute_command(
                host=os.environ.get("SSH_HOST", ""),
                username=os.environ.get("SSH_USER", ""),
                key_path=os.environ.get("SSH_KEY_PATH", ""),
                command=reply[4:].strip(),
            )

        # Convert reply to speech or let Twilio speak it
        speech = tts.synthesize_speech(reply)

        vr = VoiceResponse()
        if speech is None:
            vr.say(reply)
        else:
            # In production, upload speech bytes somewhere and <Play> the URL
            vr.say(reply)
        vr.hangup()
        return Response(str(vr), mimetype="text/xml")
