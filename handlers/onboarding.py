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
        ans = (body or "").strip().lower()
        if ans in ("yes", "y"):  # already has account
            _clear_state(phone)
            return (
                "Great — you're all set. You can text 'pay' for a secure billing link, "
                "or just start chatting. If you need help, reply 'help'."
            )
        if ans in ("no", "n"):
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
        ans = (speech or "").strip().lower()
        if ans in ("yes", "y", "yeah", "yep"):
            _clear_state(phone)
            return (
                "Great — you're already set up. If you'd like to handle billing now, say pay. "
                "Otherwise, ask me anything."
            )
        if ans in ("no", "n", "nope"):
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
    return ""
