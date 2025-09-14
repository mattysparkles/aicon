"""Flask routes for handling Twilio voice calls."""

from flask import Response, request
from twilio.twiml.voice_response import VoiceResponse

from . import gpt_agent
from utils.transcript_logger import log_transcript


def init_app(app):
    """Register routes on the given Flask application."""

    @app.route("/voice", methods=["POST"])
    def voice() -> Response:
        """Handle incoming voice calls from Twilio.

        The first request from Twilio will not contain a ``SpeechResult``. In that
        case we respond with a ``<Gather>`` prompting the caller to speak. When
        Twilio posts the speech transcription back to this endpoint, the text is
        sent to GPT for a reply which is then spoken back to the caller.
        """

        vr = VoiceResponse()

        speech_text = request.form.get("SpeechResult")
        caller = request.form.get("From", "unknown")

        # If no speech has been captured yet, prompt the user to speak.
        if not speech_text:
            gather = vr.gather(
                input="speech",
                timeout=5,
                action="/voice",
                method="POST",
            )
            gather.say(
                "Welcome to the AI assistant. Please speak after the tone.",
                voice="Polly.Joanna",
            )
            return Response(str(vr), mimetype="text/xml")

        # We have speech; send it to GPT and respond with the result.
        try:
            reply = gpt_agent.get_gpt_response(speech_text)
        except Exception:  # pragma: no cover - external service error
            vr.say(
                "Sorry, there was an error processing your request.",
                voice="Polly.Joanna",
            )
            return Response(str(vr), mimetype="text/xml")

        # Log the conversation for later review.
        try:
            log_transcript(caller=caller, question=speech_text, reply=reply)
        except Exception:  # pragma: no cover - logging should not fail call
            pass

        vr.say(reply, voice="Polly.Joanna")
        return Response(str(vr), mimetype="text/xml")

