"""Flask routes for handling Twilio voice calls."""

import os
from flask import Response, request
from twilio.twiml.voice_response import VoiceResponse

from . import email, gpt_agent, sms, ssh


def init_app(app):
    @app.route("/voice", methods=["GET", "POST"])
    def voice() -> Response:
        """Handle incoming calls and speech input."""
        speech_text = request.form.get("SpeechResult")

        if not speech_text:
            vr = VoiceResponse()
            gather = vr.gather(
                input="speech",
                action="/voice",
                method="POST",
            )
            gather.say("Welcome to the AI assistant. Please speak after the tone.")
            return Response(str(vr), mimetype="text/xml")

        reply = gpt_agent.chat_completion(
            [
                {"role": "system", "content": "You are a helpful voice assistant."},
                {"role": "user", "content": speech_text},
            ]
        )

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

        vr = VoiceResponse()
        vr.say(reply)
        vr.hangup()
        return Response(str(vr), mimetype="text/xml")
