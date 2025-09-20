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
from utils.models import Interaction, User, Subscription, UserPreference
from utils.transcript_logger import log_transcript
from utils.job_store import set_job_result, get_job_result, job_exists
from utils.call_state import (
    get_state as get_call_state,
    touch_activity,
    mark_greeted,
    set_warning as set_call_warning,
)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except Exception:
        return default


def init_app(app):
    """Register routes on the given Flask application."""
    logger = logging.getLogger(__name__)

    def _play_elabs(target, text: str, from_number: str) -> None:
        """Play ElevenLabs-rendered audio, with optional Twilio <Say> fallback.

        Set ALLOW_TWILIO_SAY_FALLBACK=true to allow <Say> on failures.
        """
        allow_say = (os.environ.get("ALLOW_TWILIO_SAY_FALLBACK", "false").lower() == "true")
        voice_id = get_user_voice_id(from_number) or os.environ.get("ELEVENLABS_VOICE_ID")
        try:
            if not voice_id:
                if allow_say:
                    target.say(text)
                return
            rel = tts.generate_elevenlabs_voice(text, voice_id)
            base = request.url_root.rstrip("/")
            target.play(f"{base}/{rel.lstrip('/')}")
        except Exception:
            if allow_say:
                try:
                    target.say(text)
                except Exception:
                    pass

    @app.route("/voice", methods=["POST"])
    def voice() -> Response:
        """Handle incoming voice calls from Twilio."""
        try:
            voice_requests.inc()
            vr = VoiceResponse()

            speech_text = request.form.get("SpeechResult")
            caller = request.form.get("From", "unknown")
            to_number = request.form.get("To", "")
            call_sid = request.form.get("CallSid", "")

            # If we're in an onboarding flow and the user pressed DTMF, treat digits as input
            try:
                if not speech_text:
                    digits = request.form.get("Digits")
                    if digits and onboarding._get_state(caller):  # type: ignore[attr-defined]
                        speech_text = digits
            except Exception:
                pass

            # Update activity timer when we receive speech or DTMF
            if call_sid and (request.form.get("SpeechResult") or request.form.get("Digits")):
                touch_activity(call_sid)

            # Intercept calls to assigned user numbers if suspended/unpaid
            def _is_blocked_call(target_number: str) -> tuple[bool, str | None]:
                if not target_number:
                    return False, None
                onboarding_number = os.environ.get("ONBOARDING_PHONE_NUMBER")
                if onboarding_number and target_number == onboarding_number:
                    return False, None
                with db_session() as s:
                    u = s.query(User).filter(User.assigned_number == target_number).first()
                    if not u:
                        return False, None
                    # Explicit suspension via preference
                    pref = (
                        s.query(UserPreference)
                        .filter(UserPreference.user_id == u.phone, UserPreference.key == "suspended")
                        .first()
                    )
                    if pref and (pref.value or "").lower() == "on":
                        return True, u.phone
                    # Simple unpaid heuristic: latest subscription not active
                    sub = (
                        s.query(Subscription)
                        .filter(Subscription.phone == u.phone)
                        .order_by(Subscription.created_at.desc())
                        .first()
                    )
                    if not sub or (sub.status or "").lower() != "active":
                        return True, u.phone
                    return False, u.phone

            blocked, owner_phone = _is_blocked_call(to_number)
            if blocked and not speech_text:
                try:
                    _play_elabs(vr, "This number is inactive due to billing.", caller)
                except Exception:
                    pass
                gather = vr.gather(input="dtmf", action="/voice/suspended_action", method="POST", num_digits=1, timeout=_env_int("GATHER_TIMEOUT", 8))
                try:
                    _play_elabs(gather, "Press 1 to get a payment link by text, or press 2 to pay by phone now.", caller)
                except Exception:
                    pass
                # Stash owner in session-like via <Gather> speechless; alternatively, we'll look it up again on action
                return Response(str(vr), mimetype="text/xml")

            # Check usage toggle (paused). If paused, play pre-recorded notice and keep the call open.
            from utils.models import User as U
            usage_paused = False
            u = None
            try:
                with db_session() as s:
                    if to_number:
                        u = s.query(U).filter(U.assigned_number == to_number).first()
                    if not u:
                        u = s.query(U).filter(U.phone == caller).first()
                    if u:
                        usage_paused = (getattr(u, "usage_paused", "false") or "false").lower() == "true"
            except Exception:
                usage_paused = False
            if usage_paused and not speech_text:
                # Try to play static MP3; fallback to ElevenLabs TTS
                audio_rel = os.environ.get("USAGE_PAUSED_AUDIO", "static/audio/usage_paused.mp3")
                base = request.url_root.rstrip("/")
                try:
                    vr.play(f"{base}/{audio_rel.lstrip('/')}")
                except Exception:
                    try:
                        _play_elabs(
                            vr,
                            "This account is temporarily unavailable. The cost of this service has exceeded the current balance. Please check back soon or help by signing up new users to keep things running. We appreciate your support!",
                            caller,
                        )
                    except Exception:
                        pass
                # Optional bonus: text the owner
                try:
                    if u:
                        sms_sender.enqueue_sms(u.phone, "Heads up: your account usage is paused due to limits. Reply 'pay' for a checkout link.")
                except Exception:
                    pass
                gather = vr.gather(input="speech dtmf", action="/voice", method="POST", speech_timeout="auto", timeout=_env_int("GATHER_TIMEOUT", 8))
                return Response(str(vr), mimetype="text/xml")

            # If no speech has been captured yet, greet and prompt the caller.
            if not speech_text:
                gather = vr.gather(
                    input="speech dtmf",
                    action="/voice",
                    method="POST",
                    speech_timeout="auto",
                    timeout=_env_int("GATHER_TIMEOUT", 8),
                )

                to_number = request.form.get("To", "")
                onboarding_number = os.environ.get("ONBOARDING_PHONE_NUMBER")
                master_number = os.environ.get("MASTER_PHONE_NUMBER")
                if onboarding_number and to_number == onboarding_number and (not master_number or to_number != master_number):
                    # Auto-start onboarding on the onboarding number with a longer intro
                    from . import onboarding as onboard_mod  # lazy to avoid cycle
                    # Mark the flow context so downstream logic can tailor prompts
                    onboard_mod._set_state(caller, "ask_has_account", {"line": "onboarding"})  # type: ignore
                    # Prefer a dedicated onboarding greeting if provided
                    long_intro = os.environ.get("ONBOARDING_GREETING_TEXT")
                    if long_intro:
                        greeting_text = long_intro.strip()
                        if not greeting_text.rstrip().endswith(("?", ".", "!")):
                            greeting_text += "."
                        greeting_text += " Do you already have an AICon account? Please say yes or no."
                    else:
                        # Fallback to standard prompt for this step
                        greeting_text = onboard_mod.voice_prompt("ask_has_account")
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

                # Only greet once per call; then enter idle-check loop
                greeted = False
                if call_sid:
                    try:
                        greeted = bool(get_call_state(call_sid).get("greeted"))
                    except Exception:
                        greeted = False
                if not greeted:
                    try:
                        # For calls to the onboarding number, add a brief pre-prompt pause
                        onboarding_number2 = os.environ.get("ONBOARDING_PHONE_NUMBER")
                        pre_len = _env_int("PRE_PROMPT_PAUSE_SECONDS", 3)
                        if onboarding_number2 and to_number == onboarding_number2 and pre_len > 0:
                            try:
                                gather.pause(length=pre_len)
                            except Exception:
                                pass
                        _play_elabs(gather, greeting_text, caller)
                    except Exception:
                        pass
                    if call_sid:
                        mark_greeted(call_sid)
                # After listening, poll idle check to handle silence warnings
                vr.redirect("/voice/idle_check", method="POST")
                return Response(str(vr), mimetype="text/xml")

            # Enforce security phrase for assigned user numbers if required
            try:
                to_number2 = request.form.get("To", "")
                if to_number2:
                    with db_session() as s:
                        u = s.query(User).filter(User.assigned_number == to_number2).first()
                        if u:
                            # Skip enforcement for master number if configured
                            master_number2 = os.environ.get("MASTER_PHONE_NUMBER")
                            if master_number2 and to_number2 == master_number2:
                                pass
                            else:
                                # Determine per-user requirement (preference overrides env)
                                pref = (
                                    s.query(UserPreference)
                                    .filter(UserPreference.user_id == (u.phone or ""), UserPreference.key == "security_required")
                                    .first()
                                )
                                pref_on = ((pref.value if pref else "").lower() in ("on", "true", "1", "yes"))
                                require_sec_env = (os.environ.get("REQUIRE_SECURITY_FOR_ASSIGNED", "true").lower() == "true")
                                require_sec = pref_on or require_sec_env
                                if require_sec:
                                    from utils.models import SecurityPhrase
                                    sp = s.query(SecurityPhrase).filter(SecurityPhrase.phone == (u.phone or "")).first()
                                    if sp:
                                        csid = request.form.get("CallSid", "")
                                        st = get_call_state(csid)
                                        if not st.get("verified"):
                                            try:
                                                _play_elabs(vr, "Please say your security phrase now, or enter digits.", caller)
                                            except Exception:
                                                pass
                                            # Mark the intended owner so /voice/security_verify checks the right record
                                            try:
                                                if csid:
                                                    from utils.call_state import set_state as _set_state
                                                    st["verify_owner"] = u.phone or ""
                                                    _set_state(csid, st)
                                            except Exception:
                                                pass
                                            gather = vr.gather(input="speech dtmf", action="/voice/security_verify", method="POST", speech_timeout="auto", timeout=_env_int("GATHER_TIMEOUT", 8))
                                            return Response(str(vr), mimetype="text/xml")
            except Exception:
                pass

            # Inspect for onboarding or payment intents
            lower = (speech_text or "").strip().lower()
            caller_state = onboarding._get_state(caller)  # type: ignore
            if lower in ("signup", "sign up", "onboard") or caller_state:
                if not caller_state:
                    onboarding_starts.inc()
                prompt = onboarding.handle_voice_input(caller, speech_text)
                gather = vr.gather(input="speech dtmf", action="/voice", method="POST", speech_timeout="auto", timeout=_env_int("GATHER_TIMEOUT", 8))
                if prompt:
                    try:
                        _play_elabs(gather, prompt, caller)
                    except Exception:
                        pass
                return Response(str(vr), mimetype="text/xml")

            if lower.startswith("set pass") or lower.startswith("set password"):
                # Start security setup flow: ask for phrase via speech or DTMF (use ElevenLabs)
                try:
                    _play_elabs(vr, "Okay. Please say your security phrase after the tone. You can also enter digits.", caller)
                except Exception:
                    pass
                gather = vr.gather(input="speech dtmf", action="/voice/security_set", method="POST", speech_timeout="auto", timeout=_env_int("GATHER_TIMEOUT", 8))
                return Response(str(vr), mimetype="text/xml")

            if lower.startswith("verify pass") or lower.startswith("verify password"):
                try:
                    _play_elabs(vr, "Please say your security phrase now, or enter digits.", caller)
                except Exception:
                    pass
                gather = vr.gather(input="speech dtmf", action="/voice/security_verify", method="POST", speech_timeout="auto", timeout=_env_int("GATHER_TIMEOUT", 8))
                return Response(str(vr), mimetype="text/xml")

            if lower in ("pay", "payment", "subscribe"):
                # Use Twilio <Pay> to collect card securely
                payments_started.inc()
                try:
                    _play_elabs(vr, "Okay, let's take your payment now.", caller)
                except Exception:
                    pass
                pay = vr.pay(
                    charge_amount=None,
                    action="/voice/pay_result",
                    payment_connector=os.environ.get("TWILIO_PAY_CONNECTOR"),
                )
                # Replace Twilio <Say> prompts with pre-rendered ElevenLabs audio or fallback to <Say>
                allow_say = (os.environ.get("ALLOW_TWILIO_SAY_FALLBACK", "false").lower() == "true")
                def set_prompt(field: str, text: str):
                    try:
                        voice_id = get_user_voice_id(caller) or os.environ.get("ELEVENLABS_VOICE_ID")
                        if voice_id:
                            base = request.url_root.rstrip("/")
                            rel = tts.generate_elevenlabs_voice(text, voice_id)
                            pay.prompt(for_=field, play=f"{base}/{rel.lstrip('/')}")
                            return
                    except Exception:
                        pass
                    if allow_say:
                        try:
                            pay.prompt(for_=field, say=text)
                        except Exception:
                            pass
                set_prompt("payment-card-number", "Please enter or say your card number.")
                set_prompt("expiration-date", "Please say the expiration date, month and year.")
                set_prompt("security-code", "Please say the security code.")
                set_prompt("postal-code", "Please say your billing postal code.")
                return Response(str(vr), mimetype="text/xml")

            # We have speech; kick off background job to compute reply + TTS
            job_id = uuid.uuid4().hex
            call_sid = request.form.get("CallSid", "")
            from_number = request.form.get("From", "")
            to_number = request.form.get("To", "")

            def worker(job: str, user_text: str, csid: str, frm: str, to: str):
                try:
                    # Build GPT reply with memory recall if enabled
                    reply_text = gpt_agent.get_gpt_response_with_memory(frm, user_text)
                    # Resolve voice preference
                    voice_id = get_user_voice_id(frm) or os.environ.get("ELEVENLABS_VOICE_ID")
                    audio_rel_path = None
                    if voice_id:
                        try:
                            audio_rel_path = tts.generate_elevenlabs_voice(reply_text, voice_id)
                        except Exception:
                            audio_rel_path = None
                    if audio_rel_path:
                        set_job_result(job, audio_rel_path)
                    else:
                        # As a last-resort fallback, return the text
                        set_job_result(job, f"TEXT:{reply_text}")
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
                            # Also persist to conversations table for memory recall
                            from utils.models import User as U, Conversation as C
                            u = s.query(U).filter(U.phone == frm).first()
                            uid = getattr(u, "id", None)
                            if uid:
                                s.add(C(user_id=uid, role="user", message=user_text))
                                s.add(C(user_id=uid, role="ai", message=reply_text))
                    except Exception:
                        logger.exception("Failed to record interaction to DB")
                except Exception as exc:
                    # Store a sentinel so /play can fallback to a generic message if needed (avoid Polly)
                    set_job_result(job, f"ERROR:{exc}")

            threading.Thread(
                target=worker,
                args=(job_id, speech_text, call_sid, from_number, to_number),
                daemon=True,
            ).start()

            # Mark activity since the caller spoke
            if call_sid:
                try:
                    touch_activity(call_sid)
                except Exception:
                    pass
            # Acknowledge by immediately polling /play (no filler speech yet)
            vr.redirect(f"/play?job={job_id}&n=0&hold=0", method="POST")
            return Response(str(vr), mimetype="text/xml")
        
        
        except Exception as exc:
            logger.exception("/voice handler error: %s", exc)
            # Never 500 to Twilio; provide a gentle nudge and continue
            try:
                vr = VoiceResponse()
                _play_elabs(vr, "I hit a snag. Please ask again.", request.form.get("From", ""))
                gather = vr.gather(input="speech dtmf", action="/voice", method="POST", speech_timeout="auto", timeout=_env_int("GATHER_TIMEOUT", 8))
                return Response(str(vr), mimetype="text/xml")
            except Exception:
                # As a last-ditch, return minimal TwiML
                fallback = VoiceResponse()
                fallback.pause(length=1)
                fallback.redirect("/voice", method="POST")
                return Response(str(fallback), mimetype="text/xml")

    # The direct reply branch is now handled by /play polling.

    @app.route("/play", methods=["GET", "POST"])
    def play_route() -> Response:
        try:
            vr = VoiceResponse()
            job_id = request.values.get("job", "")
            try:
                n = int(request.values.get("n", "0") or 0)
            except Exception:
                n = 0
            hold = request.values.get("hold", "0")
            if not job_id:
                try:
                    _play_elabs(vr, "Sorry, I lost track of that request.", request.values.get("From", ""))
                except Exception:
                    pass
                gather = vr.gather(input="speech dtmf", action="/voice", method="POST", speech_timeout="auto", timeout=_env_int("GATHER_TIMEOUT", 8))
                return Response(str(vr), mimetype="text/xml")
            
            result = get_job_result(job_id)
            if result is None:
                # Not ready yet; poll again shortly
                vr.pause(length=2)
                threshold = _env_int("HOLD_MESSAGE_THRESHOLD", 10)
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
                        # If no voice_id, skip audio to avoid non-brand voice
                    except Exception:
                        # Strict mode: do not play any other TTS
                        pass
                    vr.redirect(f"/play?job={job_id}&n={n+1}&hold=1", method="POST")
                else:
                    vr.redirect(f"/play?job={job_id}&n={n+1}&hold={hold}", method="POST")
                return Response(str(vr), mimetype="text/xml")

            if result.startswith("TEXT:"):
                # Prefer ElevenLabs for text fallback to keep brand voice
                text = result[5:]
                try:
                    _play_elabs(vr, text, request.values.get("From", ""))
                except Exception:
                    pass
                gather = vr.gather(input="speech dtmf", action="/voice", method="POST", speech_timeout="auto", timeout=_env_int("GATHER_TIMEOUT", 8))
                return Response(str(vr), mimetype="text/xml")

            if result.startswith("ERROR:"):
                # Last-resort generic error; nudge and continue (brand voice)
                try:
                    _play_elabs(vr, "Sorry, I hit a snag. Please try again.", request.values.get("From", ""))
                except Exception:
                    pass
                gather = vr.gather(input="speech dtmf", action="/voice", method="POST", speech_timeout="auto", timeout=_env_int("GATHER_TIMEOUT", 8))
                return Response(str(vr), mimetype="text/xml")

            # Play the ready audio and continue multi-turn
            base = request.url_root.rstrip("/")
            file_url = f"{base}/{result.lstrip('/')}"
            vr.play(file_url)
            gather = vr.gather(input="speech dtmf", action="/voice", method="POST", speech_timeout="auto", timeout=_env_int("GATHER_TIMEOUT", 8))
            return Response(str(vr), mimetype="text/xml")
        except Exception:
            # Never 500 to Twilio; provide a gentle nudge and continue
            fallback = VoiceResponse()
            try:
                _play_elabs(fallback, "One moment while I prepare your answer.", request.values.get("From", ""))
            except Exception:
                pass
            fallback.pause(length=2)
            fallback.redirect(request.full_path or "/play", method="POST")
            return Response(str(fallback), mimetype="text/xml")

    @app.route("/voice/idle_check", methods=["POST"])
    def voice_idle_check() -> Response:
        """Enforce 90s silence policy with staged warnings.

        Called after short <Gather> timeouts. Checks the caller's last activity
        and warns at 75/80/85 seconds, then disconnects at 90 seconds if still
        silent. Any speech or key press resets the timer in /voice.
        """
        vr = VoiceResponse()
        call_sid = request.form.get("CallSid", "")
        from_number = request.form.get("From", "")
        try:
            import time as _time
            st = get_call_state(call_sid)
            last = float(st.get("last_activity", 0))
            last_warn = int(st.get("last_warning", 0))
            elapsed = max(0, int(_time.time() - last))

            if elapsed >= 90:
                try:
                    _play_elabs(
                        vr,
                        "Since I don’t hear anything, I’ll disconnect now. Don’t worry — you can pick up where we left off on your next call or text.",
                        from_number,
                    )
                except Exception:
                    pass
                vr.hangup()
                return Response(str(vr), mimetype="text/xml")

            if elapsed >= 85 and last_warn < 5:
                try:
                    _play_elabs(
                        vr,
                        "Five seconds left without hearing anything. Say anything or press any key to stay connected.",
                        from_number,
                    )
                except Exception:
                    pass
                set_call_warning(call_sid, 5)
                vr.redirect("/voice", method="POST")
                return Response(str(vr), mimetype="text/xml")

            if elapsed >= 80 and last_warn < 10:
                try:
                    _play_elabs(
                        vr,
                        "Still here — I’ll disconnect in 10 seconds if I don’t hear anything. You can say anything or press any key to stay connected.",
                        from_number,
                    )
                except Exception:
                    pass
                set_call_warning(call_sid, 10)
                vr.redirect("/voice", method="POST")
                return Response(str(vr), mimetype="text/xml")

            if elapsed >= 75 and last_warn < 15:
                try:
                    _play_elabs(
                        vr,
                        "I haven’t heard anything. I’ll disconnect in 15 seconds. You can say anything or press any key to stay connected.",
                        from_number,
                    )
                except Exception:
                    pass
                set_call_warning(call_sid, 15)
                vr.redirect("/voice", method="POST")
                return Response(str(vr), mimetype="text/xml")

            # No warning yet; keep gathering input
            vr.redirect("/voice", method="POST")
            return Response(str(vr), mimetype="text/xml")
        except Exception:
            vr.redirect("/voice", method="POST")
            return Response(str(vr), mimetype="text/xml")

    @app.route("/voice/security_set", methods=["POST"])
    def voice_security_set() -> Response:
        vr = VoiceResponse()
        phrase = request.form.get("SpeechResult") or request.form.get("Digits") or ""
        phone = request.form.get("From", "")
        call_sid = request.form.get("CallSid", "")
        if call_sid and phrase.strip():
            try:
                touch_activity(call_sid)
            except Exception:
                pass
        if not phrase.strip():
            try:
                _play_elabs(vr, "I didn't catch that. Let's try again.", phone)
            except Exception:
                pass
            gather = vr.gather(input="speech dtmf", action="/voice/security_set", method="POST", speech_timeout="auto", timeout=_env_int("GATHER_TIMEOUT", 8))
            return Response(str(vr), mimetype="text/xml")
        security_handlers.set_phrase(phone, phrase.strip(), method=("dtmf" if request.form.get("Digits") else "speech"))
        try:
            _play_elabs(vr, "Security phrase saved.", phone)
        except Exception:
            pass
        # Mark verified flag in call state if passed
        if ok and call_sid:
            try:
                st = get_call_state(call_sid)
                st["verified"] = True
                from utils.call_state import set_state as _set_state
                _set_state(call_sid, st)
            except Exception:
                pass
        gather = vr.gather(input="speech dtmf", action="/voice", method="POST", speech_timeout="auto", timeout=_env_int("GATHER_TIMEOUT", 8))
        return Response(str(vr), mimetype="text/xml")

    @app.route("/voice/security_verify", methods=["POST"])
    def voice_security_verify() -> Response:
        vr = VoiceResponse()
        phrase = request.form.get("SpeechResult") or request.form.get("Digits") or ""
        phone = request.form.get("From", "")
        call_sid = request.form.get("CallSid", "")
        if call_sid and phrase.strip():
            try:
                touch_activity(call_sid)
            except Exception:
                pass
        ok = False
        if phrase.strip():
            # If this call targets an assigned number with enforcement, verify against the owner's phrase
            target_phone = phone
            try:
                to_number3 = request.form.get("To", "")
                if to_number3:
                    with db_session() as s:
                        u = s.query(User).filter(User.assigned_number == to_number3).first()
                        if u and (u.phone or ""):
                            target_phone = u.phone or phone
            except Exception:
                pass
            ok = security_handlers.verify_phrase(target_phone, phrase.strip())
        if ok:
            try:
                _play_elabs(vr, "Security check passed.", phone)
            except Exception:
                pass
        else:
            try:
                _play_elabs(vr, "Security check failed.", phone)
            except Exception:
                pass
        gather = vr.gather(input="speech dtmf", action="/voice", method="POST", speech_timeout="auto", timeout=_env_int("GATHER_TIMEOUT", 8))
        return Response(str(vr), mimetype="text/xml")

    @app.route("/voice/suspended_action", methods=["POST"])
    def voice_suspended_action() -> Response:
        vr = VoiceResponse()
        digit = (request.form.get("Digits") or "").strip()
        caller = request.form.get("From", "")
        to_number = request.form.get("To", "")
        call_sid = request.form.get("CallSid", "")
        if call_sid and digit:
            try:
                touch_activity(call_sid)
            except Exception:
                pass
        # Re-identify owner by destination number
        owner_phone = None
        try:
            with db_session() as s:
                u = s.query(User).filter(User.assigned_number == to_number).first()
                owner_phone = getattr(u, 'phone', None)
        except Exception:
            owner_phone = None
        if digit == "1":
            # Send Stripe checkout link via SMS to owner
            try:
                from .billing import checkout_link as _cl
                from flask import json as fjson
                # Default plan to 'basic'; allow crypto discount env if desired via facility later
                with app.test_request_context():
                    request.json = {  # type: ignore
                        "phone": owner_phone or caller,
                        "plan": "basic",
                        "crypto": False,
                    }
                    res = _cl()
                    data = fjson.loads(res.get_data(as_text=True))
                    url = data.get("url")
                if url and owner_phone:
                    try:
                        sms_sender.enqueue_sms(owner_phone, f"AICon payment link: {url}")
                    except Exception:
                        pass
                try:
                    _play_elabs(vr, "A secure payment link has been sent by text.", caller)
                except Exception:
                    pass
            except Exception:
                try:
                    _play_elabs(vr, "Sorry, I couldn't create a payment link.", caller)
                except Exception:
                    pass
            # Continue the call instead of hanging up; listen for next action
            gather = vr.gather(input="speech dtmf", action="/voice", method="POST", speech_timeout="auto", timeout=_env_int("GATHER_TIMEOUT", 8))
            return Response(str(vr), mimetype="text/xml")
        elif digit == "2":
            # Start Twilio Pay in-call
            payments_started.inc()
            try:
                _play_elabs(vr, "Okay, let's take your payment now.", caller)
            except Exception:
                pass
            pay = vr.pay(
                charge_amount=None,
                action="/voice/pay_result",
                payment_connector=os.environ.get("TWILIO_PAY_CONNECTOR"),
            )
            # Replace Twilio <Say> prompts with pre-rendered ElevenLabs audio
            try:
                voice_id = get_user_voice_id(caller) or os.environ.get("ELEVENLABS_VOICE_ID")
                base = request.url_root.rstrip("/")
                if voice_id:
                    card_url = f"{base}/{tts.generate_elevenlabs_voice('Please enter or say your card number.', voice_id).lstrip('/')}"
                    exp_url = f"{base}/{tts.generate_elevenlabs_voice('Please say the expiration date, month and year.', voice_id).lstrip('/')}"
                    cvv_url = f"{base}/{tts.generate_elevenlabs_voice('Please say the security code.', voice_id).lstrip('/')}"
                    zip_url = f"{base}/{tts.generate_elevenlabs_voice('Please say your billing postal code.', voice_id).lstrip('/')}"
                    pay.prompt(for_="payment-card-number", play=card_url)
                    pay.prompt(for_="expiration-date", play=exp_url)
                    pay.prompt(for_="security-code", play=cvv_url)
                    pay.prompt(for_="postal-code", play=zip_url)
                else:
                    # If no ElevenLabs voice is configured, keep minimal Twilio prompts disabled to preserve brand voice
                    pass
            except Exception:
                # If ElevenLabs rendering fails, do not fall back to <Say>; skip prompts
                pass
            return Response(str(vr), mimetype="text/xml")
        else:
            try:
                _play_elabs(vr, "I didn't get that.", caller)
            except Exception:
                pass
            gather = vr.gather(input="dtmf", action="/voice/suspended_action", method="POST", num_digits=1, timeout=_env_int("GATHER_TIMEOUT", 8))
            try:
                _play_elabs(gather, "Press 1 to get a payment link by text, or press 2 to pay by phone now.", caller)
            except Exception:
                pass
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
            # Activate subscription and clear suspension
            try:
                from utils.models import Subscription, UserPreference
                with db_session() as s:
                    sub = s.query(Subscription).filter(Subscription.phone == phone).order_by(Subscription.created_at.desc()).first()
                    if sub:
                        sub.status = "active"
                    else:
                        s.add(Subscription(phone=phone, plan=os.environ.get("DEFAULT_PHONE_PAY_PLAN", "basic"), provider="twilio_pay", status="active"))
                    pref = s.query(UserPreference).filter(UserPreference.user_id == phone, UserPreference.key == "suspended").first()
                    if pref:
                        pref.value = "off"
            except Exception:
                pass
            try:
                _play_elabs(vr, "Payment received. Thank you!", phone)
            except Exception:
                pass
        else:
            try:
                _play_elabs(vr, "I couldn't complete the payment. You can also text pay for a secure link.", phone)
            except Exception:
                pass
        gather = vr.gather(input="speech dtmf", action="/voice", method="POST", speech_timeout="auto", timeout=_env_int("GATHER_TIMEOUT", 8))
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

            # Normalize for command parsing
            lower = body.lower()

            # If texting an assigned user number and security is required, enforce verification
            try:
                if to_number:
                    with db_session() as s:
                        owner = s.query(User).filter(User.assigned_number == to_number).first()
                        if owner:
                            master_number3 = os.environ.get("MASTER_PHONE_NUMBER")
                            if not (master_number3 and to_number == master_number3):
                                # Per-user preference overrides env default
                                pref = (
                                    s.query(UserPreference)
                                    .filter(UserPreference.user_id == (owner.phone or ""), UserPreference.key == "security_required")
                                    .first()
                                )
                                pref_on = ((pref.value if pref else "").lower() in ("on", "true", "1", "yes"))
                                require_sec_env = (os.environ.get("REQUIRE_SECURITY_FOR_ASSIGNED", "true").lower() == "true")
                                from utils.models import SecurityPhrase
                                sp = s.query(SecurityPhrase).filter(SecurityPhrase.phone == (owner.phone or "")).first()
                                if (pref_on or require_sec_env) and sp:
                                    # Allow explicit verify commands against the owner's phrase
                                    if lower.startswith("verify pass ") or lower.startswith("verify password "):
                                        phrase = body.split(maxsplit=2)[2] if len(body.split(maxsplit=2)) == 3 else ""
                                        ok = security_handlers.verify_phrase(owner.phone or from_number, phrase.strip()) if phrase else False
                                        resp = MessagingResponse()
                                        resp.message(f"Security check: {'Pass' if ok else 'Fail'}")
                                        return Response(str(resp), mimetype="text/xml")
                                    # Otherwise, prompt for verification and stop
                                    resp = MessagingResponse()
                                    resp.message("Please verify by replying: verify pass <your phrase>")
                                    return Response(str(resp), mimetype="text/xml")
            except Exception:
                pass

            # Help / --help / -h
            if lower in ("help", "--help", "-h"):
                try:
                    lines = brand_cfg.sms_help_lines()
                except Exception:
                    lines = [
                        "AICon SMS Commands:",
                        "- help: show this menu",
                        "- signup | onboard | sign up: start onboarding",
                        "- pay [plan] [crypto]: get a secure payment link",
                        "- memory on|off: enable or disable memory",
                        "- pause on|off: pause or resume usage",
                    ]
                resp = MessagingResponse()
                resp.message("\n".join(lines))
                return Response(str(resp), mimetype="text/xml")

            # Handle voice management commands
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

            # Auto-start onboarding on the onboarding number (unless also master)
            master_number = os.environ.get("MASTER_PHONE_NUMBER")
            if onboarding_number and to_number == onboarding_number and (not master_number or to_number != master_number):
                # Ensure onboarding state is tagged with the onboarding line context
                try:
                    st = onboarding._get_state(from_number)  # type: ignore[attr-defined]
                    if not st:
                        onboarding._set_state(from_number, "ask_has_account", {"line": "onboarding"})  # type: ignore[attr-defined]
                    else:
                        import json as _json
                        data = _json.loads((st.data or "{}"))
                        if (data.get("line") or "") != "onboarding":
                            onboarding._set_state(from_number, st.step, {**data, "line": "onboarding"})  # type: ignore[attr-defined]
                except Exception:
                    pass
                cont = onboarding.handle_sms(from_number, body)
                if cont:
                    resp = MessagingResponse()
                    resp.message(cont)
                    return Response(str(resp), mimetype="text/xml")
                if body.lower() not in ("pay", "subscribe") and body.lower() not in ("signup", "sign up", "onboard"):
                    # Kick off if not already in flow
                    try:
                        onboarding._set_state(from_number, "ask_has_account", {"line": "onboarding"})  # type: ignore[attr-defined]
                    except Exception:
                        pass
                    msg = onboarding.voice_prompt("ask_has_account")
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

            # Assigned number retrieval
            if lower == "number":
                try:
                    from utils.models import User as U
                    with db_session() as s:
                        u = s.query(U).filter(U.phone == from_number).first()
                        assigned = getattr(u, "assigned_number", None) if u else None
                    if assigned:
                        msg = f"Your assigned AICon number is: {assigned}"
                    else:
                        msg = "I couldn't find an assigned number for this phone yet. If you recently signed up, it may take a moment. Reply 'help' if you need support."
                except Exception as exc:
                    msg = f"Sorry, couldn't retrieve your assigned number: {exc}"
                resp = MessagingResponse()
                resp.message(msg)
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

            # Memory/Paused management commands
            if lower.startswith("memory "):
                val = lower.split(maxsplit=1)[1].strip()
                wanted = val in ("on", "true", "enable", "enabled", "1")
                try:
                    from utils.models import User as U
                    with db_session() as s:
                        u = s.query(U).filter(U.phone == from_number).first()
                        if not u:
                            u = U(phone=from_number)
                            s.add(u)
                            s.flush()
                        u.memory_enabled = "true" if wanted else "false"
                    ack = f"Memory is now {'ON' if wanted else 'OFF'}."
                except Exception as exc:
                    ack = f"Could not update memory: {exc}"
                resp = MessagingResponse()
                resp.message(ack)
                return Response(str(resp), mimetype="text/xml")

            if lower.startswith("pause "):
                val = lower.split(maxsplit=1)[1].strip()
                wanted = val in ("on", "true", "enable", "enabled", "1")
                try:
                    from utils.models import User as U
                    with db_session() as s:
                        u = s.query(U).filter(U.phone == from_number).first()
                        if not u:
                            u = U(phone=from_number)
                            s.add(u)
                            s.flush()
                        u.usage_paused = "true" if wanted else "false"
                    ack = f"Usage paused is now {'ON' if wanted else 'OFF'}."
                except Exception as exc:
                    ack = f"Could not update paused state: {exc}"
                resp = MessagingResponse()
                resp.message(ack)
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

            # Normal SMS: forward to GPT with memory and reply
            # Detect if this is the user's first SMS to send a help primer
            first_contact = False
            try:
                with db_session() as s:
                    cnt = s.query(Interaction).filter(Interaction.user_id == from_number, Interaction.input_type == "sms").count()
                    first_contact = cnt == 0
            except Exception:
                first_contact = False
            try:
                reply = gpt_agent.get_gpt_response_with_memory(from_number, body)
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

            # Send help primer on first contact
            if first_contact:
                try:
                    lines = brand_cfg.sms_help_lines()
                    sms_sender.enqueue_sms(from_number, "\n".join(lines), from_number=to_number)
                except Exception:
                    pass

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
                    # Also persist to conversations for memory recall
                    from utils.models import User as U, Conversation as C
                    u = s.query(U).filter(U.phone == from_number).first()
                    if not u:
                        # Auto-create a user shell to attach memory
                        u = U(phone=from_number)
                        s.add(u)
                        s.flush()
                    s.add(C(user_id=u.id, role="user", message=body))
                    s.add(C(user_id=u.id, role="ai", message=reply))
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
