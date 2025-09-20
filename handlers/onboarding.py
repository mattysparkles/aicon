"""Onboarding flow via SMS and Voice using a simple conversation state."""

from __future__ import annotations

import json
from typing import Dict, Optional
from datetime import datetime

from flask import request

import os
from utils import brand as brand_cfg
from utils.db import db_session
from utils.models import ConversationState, User
from dataclasses import dataclass


@dataclass
class _State:
    step: str
    data: str | None


FLOW = "onboard"


def _onboarding_intro() -> str:
    long_greeting = os.environ.get("ONBOARDING_GREETING_TEXT")
    if long_greeting:
        base = long_greeting.strip()
    else:
        base = (
            os.environ.get("GREETING_TEXT")
            or "Welcome! I'm Sparkles, your AI voice and SMS assistant."
        )
    # Always end with the account question to branch the flow
    brand_name = brand_cfg.name("AICon")
    return base.rstrip() + " " + f"Do you already have an {brand_name} account? Please say or reply YES or NO."


def _get_state(phone: str) -> Optional[_State]:
    """Return a detached snapshot of the conversation state.

    Avoid returning ORM instances outside the session to prevent
    DetachedInstanceError when accessing attributes later.
    """
    with db_session() as s:
        st = (
            s.query(ConversationState)
            .filter(ConversationState.phone == phone, ConversationState.flow == FLOW)
            .first()
        )
        if not st:
            return None
        return _State(step=st.step, data=st.data)


def _set_state(phone: str, step: str, data: Dict[str, str]) -> None:
    with db_session() as s:
        st = (
            s.query(ConversationState)
            .filter(ConversationState.phone == phone, ConversationState.flow == FLOW)
            .first()
        )
        payload = json.dumps(data)
        if st:
            st.step = step
            st.data = payload
            st.updated_at = datetime.utcnow()
        else:
            s.add(ConversationState(phone=phone, flow=FLOW, step=step, data=payload))


def _clear_state(phone: str) -> None:
    with db_session() as s:
        s.query(ConversationState).filter(
            ConversationState.phone == phone, ConversationState.flow == FLOW
        ).delete()


def start(phone: str) -> str:
    _set_state(phone, "ask_has_account", {})
    return _onboarding_intro()


def handle_sms(phone: str, body: str) -> Optional[str]:
    st = _get_state(phone)
    if not st:
        return None
    data = json.loads((st.data or "{}"))
    if st.step == "ask_has_account":
        raw = (body or "").strip().lower()
        ans = "".join(ch if (ch.isalnum() or ch.isspace()) else " " for ch in raw)
        ans = " ".join(ans.split())
        yes_tokens = {"yes", "y", "yeah", "yep", "yup", "sure", "affirmative"}
        no_tokens = {"no", "n", "nope", "nah", "negative"}
        if ans in {"1", "one"}:
            match = "yes"
        elif ans in {"2", "two"}:
            match = "no"
        else:
            words = set(ans.split())
            if words & yes_tokens or ans.startswith("y"):
                match = "yes"
            elif words & no_tokens or ans.startswith("n"):
                match = "no"
            else:
                match = None

        if match == "yes":  # already has account
            if (data.get("line") or "") == "onboarding":
                _set_state(phone, "ask_support", data)
                return (
                    "Great — since you already have an account, to speak with your AI assistant please call your individual assigned number you received when you signed up. "
                    "Do you need help retrieving that number, or do you need other support? Reply 'number', 'support', or 'no'."
                )
            _clear_state(phone)
            return (
                "Great — you're all set. You can text 'pay' for a secure billing link, or just start chatting. Reply 'help' anytime."
            )
        if match == "no":
            _set_state(phone, "ask_name", data)
            return "Okay, let's create your account. What's your full name?"
        # Nudge if not understood
        brand_name = brand_cfg.name("AICon")
        return f"Please reply YES or NO about your {brand_name} account to continue."
    if st.step == "ask_name":
        data["name"] = body.strip()
        _set_state(phone, "ask_prison_id", data)
        return "Got it. What's your prison ID?"
    if st.step == "ask_prison_id":
        data["prison_id"] = body.strip()
        _set_state(phone, "ask_affiliate", data)
        return "Thanks. If you have an affiliate code, reply with it now. If not, reply 'none'."
    if st.step == "ask_affiliate":
        code = body.strip()
        if code.lower() == "none":
            code = ""
        data["affiliate_code"] = code
        # Save user
        # Save user and track referrer (case-insensitive match on affiliate_code)
        with db_session() as s:
            # find referrer by affiliate_code case-insensitive
            ref = None
            if code:
                all_refs = s.query(User).filter(User.affiliate_code.isnot(None)).all()
                cl = code.lower()
                for ru in all_refs:
                    if (ru.affiliate_code or "").lower() == cl:
                        ref = ru
                        break
            u = s.query(User).filter(User.phone == phone).first()
            if u:
                u.name = data.get("name")
                u.prison_id = data.get("prison_id")
                u.affiliate_code = code
                if ref:
                    u.referrer_id = ref.id
            else:
                u = User(
                    phone=phone,
                    name=data.get("name"),
                    prison_id=data.get("prison_id"),
                    affiliate_code=code,
                    referrer_id=(ref.id if ref else None),
                )
                s.add(u)
        _clear_state(phone)
        return "All set! Reply 'pay' to get a billing link or say 'pay' on a call to pay by phone. Reply 'help' anytime for commands."
    if st.step == "ask_support":
        raw = (body or "").strip().lower()
        ans = "".join(ch if (ch.isalnum() or ch.isspace()) else " " for ch in raw)
        ans = " ".join(ans.split())
        words = set(ans.split())
        wants_number = ("number" in words) or ("assigned" in words) or ("retrieve" in words) or ans.startswith("num")
        wants_support = ("support" in words) or ("help" in words) or ("agent" in words)
        says_no = (ans in ("no", "nope", "nah", "n"))
        if wants_number:
            _clear_state(phone)
            return (
                "No problem. Reply 'number' from the phone you used when you signed up and I will text your assigned number back automatically. "
                "If you prefer human help, reply 'help'."
            )
        if wants_support:
            _clear_state(phone)
            return (
                "Okay. For support, reply 'help' and a teammate will follow up. "
                "You can also ask me questions here."
            )
        if says_no:
            _clear_state(phone)
            return "Okay. Thanks for reaching out!"
        return "Please reply 'number' for help retrieving your assigned number, 'support' for other help, or 'no'."
    return None


def voice_prompt(step: str) -> str:
    if step == "ask_name":
        return "Welcome! Let's get you set up. Please say your full name after the tone."
    if step == "ask_prison_id":
        return "Thanks. Please say your prison I D."
    if step == "ask_affiliate":
        return "If you have an affiliate referral code, say it now. Otherwise say none."
    if step == "ask_has_account":
        return f"Do you already have an {brand_cfg.name('AICon')} account? Please say yes or no."
    return ""


def handle_voice_input(phone: str, speech: str) -> str:
    st = _get_state(phone)
    if not st:
        _set_state(phone, "ask_has_account", {})
        return voice_prompt("ask_has_account")
    data = json.loads((st.data or "{}"))
    if st.step == "ask_has_account":
        raw = (speech or "").strip().lower()
        # Normalize punctuation and whitespace
        ans = "".join(ch if (ch.isalnum() or ch.isspace()) else " " for ch in raw)
        ans = " ".join(ans.split())  # collapse spaces

        # Accept common variants and DTMF 1/2
        yes_tokens = {"yes", "y", "yeah", "yep", "yup", "sure", "correct", "affirmative"}
        no_tokens = {"no", "n", "nope", "nah", "negative"}

        # Direct digit mapping (e.g., pressing keys during <Gather>)
        if ans in {"1", "one"}:
            match = "yes"
        elif ans in {"2", "two"}:
            match = "no"
        else:
            # Token-based and prefix-based matching
            words = set(ans.split())
            if words & yes_tokens or ans.startswith("y"):
                match = "yes"
            elif words & no_tokens or ans.startswith("n"):
                match = "no"
            # Phrase hints like "i have an account" / "i do not"
            elif "have" in words and "account" in words and ("do" in words or "i" in words):
                match = "yes"
            elif ("dont" in words or "do" in words and "not" in words) and "have" in words and "account" in words:
                match = "no"
            else:
                match = None

        if match == "yes":
            # If the user called the onboarding line, steer them to use their assigned number
            if (data.get("line") or "") == "onboarding":
                _set_state(phone, "ask_support", data)
                return (
                    "Great — since you already have an account, to speak with your AI assistant please call your individual assigned number you received when you signed up. "
                    "Do you need help retrieving that number, or do you need other support? You can say 'number', 'support', or 'no'."
                )
            _clear_state(phone)
            return (
                "Great — you're already set up. If you'd like to handle billing now, say pay. "
                "Otherwise, ask me anything."
            )
        if match == "no":
            _set_state(phone, "ask_name", data)
            return voice_prompt("ask_name")
        return f"Please say yes or no about your {brand_cfg.name('AICon')} account."
    if st.step == "ask_name":
        data["name"] = speech.strip()
        _set_state(phone, "ask_prison_id", data)
        return voice_prompt("ask_prison_id")
    if st.step == "ask_prison_id":
        data["prison_id"] = speech.strip()
        _set_state(phone, "ask_affiliate", data)
        return voice_prompt("ask_affiliate")
    if st.step == "ask_affiliate":
        code = speech.strip()
        if code.lower() in ("none", "no"):
            code = ""
        data["affiliate_code"] = code
        # Save user
        with db_session() as s:
            ref = None
            if code:
                all_refs = s.query(User).filter(User.affiliate_code.isnot(None)).all()
                cl = code.lower()
                for ru in all_refs:
                    if (ru.affiliate_code or "").lower() == cl:
                        ref = ru
                        break
            u = s.query(User).filter(User.phone == phone).first()
            if u:
                u.name = data.get("name")
                u.prison_id = data.get("prison_id")
                u.affiliate_code = code
                if ref:
                    u.referrer_id = ref.id
            else:
                u = User(
                    phone=phone,
                    name=data.get("name"),
                    prison_id=data.get("prison_id"),
                    affiliate_code=code,
                    referrer_id=(ref.id if ref else None),
                )
                s.add(u)
        _clear_state(phone)
        return "You're all set up. To pay now, say pay, or you can text pay for a link."
    if st.step == "ask_support":
        raw = (speech or "").strip().lower()
        ans = "".join(ch if (ch.isalnum() or ch.isspace()) else " " for ch in raw)
        ans = " ".join(ans.split())
        words = set(ans.split())
        # Simple routing: number retrieval, general support, or no
        wants_number = ("number" in words) or ("assigned" in words) or ("retrieve" in words) or ans.startswith("num")
        wants_support = ("support" in words) or ("help" in words) or ("agent" in words)
        says_no = (ans in ("no", "nope", "nah", "n"))
        if wants_number:
            _clear_state(phone)
            return (
                "No problem. The quickest way to get your assigned number is to text the word 'number' to this line from the phone you used when you signed up, and you'll receive it automatically. "
                "If you prefer human help, you can also text 'help'."
            )
        if wants_support:
            _clear_state(phone)
            return (
                "Okay. For support, please text the word 'help' to this number and a teammate will follow up. "
                "You can also ask me common questions here."
            )
        if says_no:
            _clear_state(phone)
            return "Okay. Thanks for calling!"
        return "Please say 'number' for help retrieving your assigned number, 'support' for other help, or 'no'."
    return ""
