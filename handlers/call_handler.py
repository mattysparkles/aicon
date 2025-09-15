"""Flask routes for handling Twilio voice and SMS in a unified webhook."""

import os
import logging
import threading
import uuid
from flask import Response, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import VoiceResponse

from . import gpt_agent, tts
from . import sms as sms_sender
from .voices import get_user_voice_id, list_voice_keywords, set_user_voice_keyword
from . import onboarding
from .billing import credit_affiliate
from . import security as security_handlers
from .metrics import sms_requests, voice_requests, sms_replies, onboarding_starts, payments_started, tts_failures
from utils.db import db_session
from utils.models import Interaction
from utils.transcript_logger import log_transcript
from utils.job_store import set_job_result, get_job_result, job_exists


def init_app(app):
    """Register routes on the given Flask application."""
    logger = logging.getLogger(__name__)

    @app.route("/voice", methods=["POST"])
    def voice() -> Response:
        """Handle incoming voice calls from Twilio.

        The first request from Twilio will not contain a ``SpeechResult``. In that
        case we respond with a ``<Gather>`` prompting the caller to speak. When
        Twilio posts the speech transcription back to this endpoint, the text is
        sent to GPT for a reply which is then spoken back to the caller.
        """

        voice_requests.inc()
        vr = VoiceResponse()

        speech_text = request.form.get("SpeechResult")
        caller = request.form.get("From", "unknown")

        # If no speech has been captured yet, greet and prompt the caller.
        if not speech_text:
            gather = vr.gather(
                input="speech",
                action="/voice",
                method="POST",
                speech_timeout="auto",
                timeout=int(os.environ.get("GATHER_TIMEOUT", "10")),
            )

            to_number = request.form.get("To", "")
            onboarding_number = os.environ.get("ONBOARDING_PHONE_NUMBER")
            if onboarding_number and to_number == onboarding_number:
                # Auto-start onboarding on the onboarding number
                from . import onboarding as onboard_mod  # lazy to avoid cycle
                onboard_mod._set_state(caller, "ask_name", {})  # type: ignore
                greeting_text = onboard_mod.voice_prompt("ask_name")
            else:
                # Dynamic greeting rotation
                greeting_variants = [
                    "Hey, it's Sparkles — what's on your mind today?",
                    "Hi! Sparkles here. How can I help?",
                    "Hey there — Sparkles ready to jump in whenever you are!",
                    "It's Sparkles. What should we tackle first?",
                ]
                env_greeting = os.environ.get("GREETING_TEXT")
                if env_greeting:
                    greeting_variants.insert(0, env_greeting)
                # Choose a variant based on caller hash to "rotate"
                idx = abs(hash(caller)) % len(greeting_variants)
                greeting_text = greeting_variants[idx]

            try:
                # Use user's preferred voice if configured
                voice_id = get_user_voice_id(caller) or os.environ.get("ELEVENLABS_VOICE_ID")
                if voice_id:
                    audio_rel_path = tts.generate_elevenlabs_voice(greeting_text, voice_id)
                else:
                    audio_rel_path = tts.generate_sparkles_voice(greeting_text)
                base = request.url_root.rstrip("/")
                file_url = f"{base}/{audio_rel_path.lstrip('/')}"
                logger.info("Greeting audio ready at %s", file_url)
                gather.play(file_url)
            except Exception as exc:
                logger.warning("Greeting TTS fallback to Polly: %s", exc)
                # Fallback to Twilio/Polly voice if ElevenLabs is unavailable.
                gather.say(greeting_text, voice="Polly.Joanna")

            # If no input, nudge the user and loop back
            gather.say("Still with me? Just say something or hang tight!", voice="Polly.Joanna")
            vr.redirect("/voice", method="POST")

            return Response(str(vr), mimetype="text/xml")

        # Inspect for onboarding or payment intents
        lower = speech_text.strip().lower()
        caller_state = onboarding._get_state(caller)  # type: ignore
        if lower in ("signup", "sign up", "onboard") or caller_state:
            if not caller_state:
                onboarding_starts.inc()
            prompt = onboarding.handle_voice_input(caller, speech_text)
            gather = vr.gather(input="speech", action="/voice", method="POST", speech_timeout="auto", timeout=8)
            if prompt:
                try:
                    voice_id = get_user_voice_id(caller) or os.environ.get("ELEVENLABS_VOICE_ID")
                    if voice_id:
                        rel = tts.generate_elevenlabs_voice(prompt, voice_id)
                        base = request.url_root.rstrip("/")
                        gather.play(f"{base}/{rel.lstrip('/')}")
                    else:
                        gather.say(prompt, voice="Polly.Joanna")
                except Exception:
                    gather.say(prompt, voice="Polly.Joanna")
            return Response(str(vr), mimetype="text/xml")

        if lower.startswith("set pass") or lower.startswith("set password"):
            # Start security setup flow: ask for phrase via speech or DTMF
            vr.say("Okay. Please say your security phrase after the tone. You can also enter digits.", voice="Polly.Joanna")
            gather = vr.gather(input="speech dtmf", action="/voice/security_set", method="POST", speech_timeout="auto", timeout=int(os.environ.get("GATHER_TIMEOUT", "10")))
            return Response(str(vr), mimetype="text/xml")

        if lower.startswith("verify pass") or lower.startswith("verify password"):
            vr.say("Please say your security phrase now, or enter digits.", voice="Polly.Joanna")
            gather = vr.gather(input="speech dtmf", action="/voice/security_verify", method="POST", speech_timeout="auto", timeout=int(os.environ.get("GATHER_TIMEOUT", "10")))
            return Response(str(vr), mimetype="text/xml")

        if lower in ("pay", "payment", "subscribe"):
            # Use Twilio <Pay> to collect card securely
            payments_started.inc()
            vr.say("Okay, let's take your payment now.", voice="Polly.Joanna")
            pay = vr.pay(charge_amount=None, action="/voice/pay_result", payment_connector=os.environ.get("TWILIO_PAY_CONNECTOR"))
            pay.prompt(for_="payment-card-number", say="Please enter or say your card number.")
            pay.prompt(for_="expiration-date", say="Please say the expiration date, month and year.")
            pay.prompt(for_="security-code", say="Please say the security code.")
            pay.prompt(for_="postal-code", say="Please say your billing postal code.")
            return Response(str(vr), mimetype="text/xml")

        # We have speech; kick off background job to compute reply + TTS
        job_id = uuid.uuid4().hex
        call_sid = request.form.get("CallSid", "")
        from_number = request.form.get("From", "")
        to_number = request.form.get("To", "")

        def worker(job: str, user_text: str, csid: str, frm: str, to: str):
            try:
                reply_text = gpt_agent.get_gpt_response(user_text)
                # Resolve voice preference
                voice_id = get_user_voice_id(frm) or os.environ.get("ELEVENLABS_VOICE_ID")
                audio_rel_path = None
                if voice_id:
                    try:
                        audio_rel_path = tts.generate_elevenlabs_voice(reply_text, voice_id)
                    except Exception as tts_exc:
                        logger.warning("ElevenLabs failed for reply; will fallback to Polly say. err=%s", tts_exc)
                if not audio_rel_path:
                    # As a fallback, return the text so /play can speak it via Polly
                    set_job_result(job, f"TEXT:{reply_text}")
                else:
                    set_job_result(job, audio_rel_path)
                try:
                    log_transcript(
                        call_sid=csid,
                        from_number=frm,
                        to_number=to,
                        user_text=user_text,
                        gpt_reply=reply_text,
                    )
                except Exception:
                    pass
                # Persist to DB
                try:
                    with db_session() as s:
                        s.add(
                            Interaction(
                                user_id=frm,
                                input_type="voice",
                                transcript=user_text,
                                response=reply_text,
                                model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                                voice_id=voice_id or "",
                            )
                        )
                except Exception:
                    logger.exception("Failed to record interaction to DB")
            except Exception as exc:
                # Store a sentinel so /play can fallback to Polly if needed
                set_job_result(job, f"ERROR:{exc}")

        threading.Thread(
            target=worker,
            args=(job_id, speech_text, call_sid, from_number, to_number),
            daemon=True,
        ).start()

        # Acknowledge by immediately polling /play (no filler speech yet)
        vr.redirect(f"/play?job={job_id}&n=0&hold=0", method="POST")
        return Response(str(vr), mimetype="text/xml")

        # The direct reply branch is now handled by /play polling.
        return Response(str(vr), mimetype="text/xml")

    @app.route("/play", methods=["GET", "POST"])
    def play_route() -> Response:
        vr = VoiceResponse()
        job_id = request.values.get("job", "")
        n = int(request.values.get("n", "0") or 0)
        hold = request.values.get("hold", "0")
        if not job_id:
            vr.say("Sorry, I lost track of that request.", voice="Polly.Joanna")
            gather = vr.gather(input="speech", action="/voice", method="POST", speech_timeout="auto", timeout=int(os.environ.get("GATHER_TIMEOUT", "10")))
            return Response(str(vr), mimetype="text/xml")

        result = get_job_result(job_id)
        if result is None:
            # Not ready yet; poll again shortly
            vr.pause(length=2)
            threshold = int(os.environ.get("HOLD_MESSAGE_THRESHOLD", "10"))
            # After threshold seconds of waiting, play a single hold message in ElevenLabs voice if available
            if (n + 1) * 2 >= threshold and hold != "1":
                try:
                    from_number = request.values.get("From", "")
                    hold_text = "One moment while I prepare your answer."
                    voice_id = get_user_voice_id(from_number) or os.environ.get("ELEVENLABS_VOICE_ID")
                    if voice_id:
                        rel = tts.generate_elevenlabs_voice(hold_text, voice_id)
                        base = request.url_root.rstrip("/")
                        vr.play(f"{base}/{rel.lstrip('/')}")
                    else:
                        vr.say(hold_text, voice="Polly.Joanna")
                except Exception:
                    vr.say("One moment while I prepare your answer.", voice="Polly.Joanna")
                vr.redirect(f"/play?job={job_id}&n={n+1}&hold=1", method="POST")
            else:
                vr.redirect(f"/play?job={job_id}&n={n+1}&hold={hold}", method="POST")
            return Response(str(vr), mimetype="text/xml")

        if result.startswith("TEXT:"):
            # Read the GPT reply via Polly
            text = result[5:]
            vr.say(text, voice="Polly.Joanna")
            gather = vr.gather(input="speech", action="/voice", method="POST", speech_timeout="auto", timeout=int(os.environ.get("GATHER_TIMEOUT", "10")))
            return Response(str(vr), mimetype="text/xml")

        if result.startswith("ERROR:"):
            # Last-resort generic error; nudge and continue
            vr.say("Sorry, I hit a snag. Please try again.", voice="Polly.Joanna")
            gather = vr.gather(input="speech", action="/voice", method="POST", speech_timeout="auto", timeout=int(os.environ.get("GATHER_TIMEOUT", "10")))
            return Response(str(vr), mimetype="text/xml")

        # Play the ready audio and continue multi-turn
        base = request.url_root.rstrip("/")
        file_url = f"{base}/{result.lstrip('/')}"
        vr.play(file_url)
        gather = vr.gather(input="speech", action="/voice", method="POST", speech_timeout="auto", timeout=int(os.environ.get("GATHER_TIMEOUT", "10")))
        return Response(str(vr), mimetype="text/xml")

    @app.route("/voice/security_set", methods=["POST"])
    def voice_security_set() -> Response:
        vr = VoiceResponse()
        phrase = request.form.get("SpeechResult") or request.form.get("Digits") or ""
        phone = request.form.get("From", "")
        if not phrase.strip():
            vr.say("I didn't catch that. Let's try again.", voice="Polly.Joanna")
            gather = vr.gather(input="speech dtmf", action="/voice/security_set", method="POST", speech_timeout="auto", timeout=int(os.environ.get("GATHER_TIMEOUT", "10")))
            return Response(str(vr), mimetype="text/xml")
        security_handlers.set_phrase(phone, phrase.strip(), method=("dtmf" if request.form.get("Digits") else "speech"))
        vr.say("Security phrase saved.", voice="Polly.Joanna")
        gather = vr.gather(input="speech", action="/voice", method="POST", speech_timeout="auto", timeout=int(os.environ.get("GATHER_TIMEOUT", "10")))
        return Response(str(vr), mimetype="text/xml")

    @app.route("/voice/security_verify", methods=["POST"])
    def voice_security_verify() -> Response:
        vr = VoiceResponse()
        phrase = request.form.get("SpeechResult") or request.form.get("Digits") or ""
        phone = request.form.get("From", "")
        ok = False
        if phrase.strip():
            ok = security_handlers.verify_phrase(phone, phrase.strip())
        if ok:
            vr.say("Security check passed.", voice="Polly.Joanna")
        else:
            vr.say("Security check failed.", voice="Polly.Joanna")
        gather = vr.gather(input="speech", action="/voice", method="POST", speech_timeout="auto", timeout=int(os.environ.get("GATHER_TIMEOUT", "10")))
        return Response(str(vr), mimetype="text/xml")

    @app.route("/voice/pay_result", methods=["POST"])
    def pay_result() -> Response:
        vr = VoiceResponse()
        status = request.form.get("Result", "") or request.form.get("PaymentStatus", "")
        amount = request.form.get("PaymentAmount", "0")
        phone = request.form.get("From", "")
        affiliate_code = None
        # Credit affiliate if present in user record
        try:
            from utils.db import db_session
            from utils.models import User
            with db_session() as s:
                u = s.query(User).filter(User.phone == phone).first()
                affiliate_code = getattr(u, "affiliate_code", None) if u else None
        except Exception:
            pass
        if (status or "").lower() in ("success", "successful", "succeeded"):
            # Best-effort affiliate credit; amount is unknown here, but we can parse cents if provided
            try:
                cents = int(float(amount) * 100) if amount else 0
                credit_affiliate(phone, cents, affiliate_code)
            except Exception:
                pass
            vr.say("Payment received. Thank you!", voice="Polly.Joanna")
        else:
            vr.say("I couldn't complete the payment. You can also text pay for a secure link.", voice="Polly.Joanna")
        gather = vr.gather(input="speech", action="/voice", method="POST", speech_timeout="auto", timeout=int(os.environ.get("GATHER_TIMEOUT", "10")))
        return Response(str(vr), mimetype="text/xml")

    @app.route("/twilio", methods=["POST"])
    def twilio_unified() -> Response:
        """Unified webhook for both SMS and Voice on the same number.

        Detects the type by presence of MessageSid/SmsSid vs CallSid and
        responds with appropriate TwiML.
        """
        if request.form.get("MessageSid") or request.form.get("SmsSid"):
            sms_requests.inc()
            # SMS flow
            body = (request.form.get("Body") or "").strip()
            from_number = request.form.get("From", "")
            to_number = request.form.get("To", "")
            onboarding_number = os.environ.get("ONBOARDING_PHONE_NUMBER")

            # Handle voice management commands
            lower = body.lower()
            if lower.startswith("voice ") or lower.startswith("upgrade voice "):
                parts = body.split(maxsplit=1)
                keyword = parts[1].strip() if len(parts) > 1 else ""
                if keyword:
                    resolved = set_user_voice_keyword(from_number, keyword)
                    msg = f"Voice set to '{keyword}'."
                else:
                    msg = "Please specify a voice keyword. Try 'voice list'."
                resp = MessagingResponse()
                resp.message(msg)
                return Response(str(resp), mimetype="text/xml")

            if lower in ("voice list", "voices", "voice help"):
                m = list_voice_keywords()
                if m:
                    lines = ["Available voices:"] + [f"- {k}" for k in m.keys()]
                    help_text = "\n".join(lines)
                else:
                    help_text = "No voices configured. Set VOICE_MAP env var."
                resp = MessagingResponse()
                resp.message(help_text)
                return Response(str(resp), mimetype="text/xml")

            # Auto-start onboarding on the onboarding number
            if onboarding_number and to_number == onboarding_number:
                cont = onboarding.handle_sms(from_number, body)
                if cont:
                    resp = MessagingResponse()
                    resp.message(cont)
                    return Response(str(resp), mimetype="text/xml")
                if body.lower() not in ("pay", "subscribe") and body.lower() not in ("signup", "sign up", "onboard"):
                    # Kick off if not already in flow
                    msg = onboarding.start(from_number)
                    resp = MessagingResponse()
                    resp.message(msg)
                    return Response(str(resp), mimetype="text/xml")

            # Facility set command
            if body.lower().startswith("facility "):
                code = body.split(maxsplit=1)[1].strip()
                try:
                    from utils.db import db_session
                    from utils.models import User
                    with db_session() as s:
                        u = s.query(User).filter(User.phone == from_number).first()
                        if u:
                            u.facility_code = code
                        else:
                            from utils.models import User as U
                            s.add(U(phone=from_number, facility_code=code))
                    msg = f"Facility set to {code}."
                except Exception as exc:
                    msg = f"Could not set facility: {exc}"
                resp = MessagingResponse()
                resp.message(msg)
                return Response(str(resp), mimetype="text/xml")

            # Security phrase via SMS
            if body.lower().startswith("set pass ") or body.lower().startswith("set password "):
                phrase = body.split(maxsplit=2)[2] if len(body.split(maxsplit=2)) == 3 else ""
                if not phrase:
                    msg = "Usage: SET PASS <your phrase>"
                else:
                    try:
                        security_handlers.set_phrase(from_number, phrase.strip(), method="text")
                        msg = "Security phrase saved."
                    except Exception as exc:
                        msg = f"Error saving phrase: {exc}"
                resp = MessagingResponse()
                resp.message(msg)
                return Response(str(resp), mimetype="text/xml")

            if body.lower().startswith("verify pass ") or body.lower().startswith("verify password "):
                phrase = body.split(maxsplit=2)[2] if len(body.split(maxsplit=2)) == 3 else ""
                ok = security_handlers.verify_phrase(from_number, phrase.strip()) if phrase else False
                msg = "Pass" if ok else "Fail"
                resp = MessagingResponse()
                resp.message(f"Security check: {msg}")
                return Response(str(resp), mimetype="text/xml")

            # Onboarding commands
            if body.lower() in ("signup", "sign up", "onboard"):
                msg = onboarding.start(from_number)
                resp = MessagingResponse()
                resp.message(msg)
                return Response(str(resp), mimetype="text/xml")

            # Continue onboarding if in progress
            cont = onboarding.handle_sms(from_number, body)
            if cont:
                resp = MessagingResponse()
                resp.message(cont)
                return Response(str(resp), mimetype="text/xml")

            # Feedback command
            if body.lower().startswith("feedback "):
                msg_text = body[9:].strip()
                try:
                    from utils.db import db_session
                    from utils.models import Feedback
                    with db_session() as s:
                        s.add(Feedback(phone=from_number, message=msg_text))
                    ack = "Thanks for the feedback! We appreciate it."
                except Exception as exc:
                    ack = f"Could not save feedback: {exc}"
                resp = MessagingResponse()
                resp.message(ack)
                return Response(str(resp), mimetype="text/xml")

            # Payment link via Stripe Checkout
            if body.lower().startswith("pay") or body.lower().startswith("subscribe"):
                # format: "pay pro crypto" or just "pay"
                parts = body.lower().split()
                plan = "basic"
                crypto = False
                if len(parts) >= 2:
                    plan = parts[1]
                if len(parts) >= 3 and parts[2] == "crypto":
                    crypto = True
                # create checkout
                try:
                    from .billing import checkout_link as _cl
                    from flask import json as fjson
                    with app.test_request_context():
                        request.json = {  # type: ignore
                            "phone": from_number,
                            "plan": plan,
                            "crypto": crypto,
                        }
                        res = _cl()
                        data = fjson.loads(res.get_data(as_text=True))
                        url = data.get("url")
                        if url:
                            msg = f"Here is your secure payment link: {url}"
                        else:
                            msg = "Sorry, I could not generate a payment link."
                except Exception as exc:
                    msg = f"Payment link error: {exc}"
                resp = MessagingResponse()
                resp.message(msg)
                return Response(str(resp), mimetype="text/xml")

            # Normal SMS: forward to GPT and reply
            try:
                reply = gpt_agent.get_gpt_response(body)
            except Exception as exc:  # pragma: no cover - external API
                reply = f"Sorry, I couldn't process that: {exc}"

            # Split reply into SMS-sized chunks (400 chars)
            parts = list(sms_sender._chunk_message(reply, limit=400))  # type: ignore
            first = parts[0] if parts else ""
            rest = parts[1:] if len(parts) > 1 else []

            # Queue remaining parts to send via REST in background
            for p in rest:
                try:
                    sms_sender.enqueue_sms(from_number, p, from_number=to_number)
                except Exception:
                    logger.exception("Failed to enqueue SMS part")

            # Log to DB
            try:
                with db_session() as s:
                    s.add(
                        Interaction(
                            user_id=from_number,
                            input_type="sms",
                            transcript=body,
                            response=reply,
                            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                        )
                    )
            except Exception:
                logger.exception("Failed to record SMS interaction to DB")

            resp = MessagingResponse()
            # Respond with just the first chunk in TwiML
            resp.message(first)
            if first:
                sms_replies.inc()
            return Response(str(resp), mimetype="text/xml")

        # Otherwise treat as a voice webhook; proxy to /voice logic
        return voice()

    @app.route("/onboard", methods=["POST"])
    def onboard_unified() -> Response:
        # Alias endpoint used for onboarding number webhooks
        return twilio_unified()
