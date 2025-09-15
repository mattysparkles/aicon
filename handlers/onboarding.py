"""Onboarding flow via SMS and Voice using a simple conversation state."""

from __future__ import annotations

import json
from typing import Dict, Optional
from datetime import datetime

from flask import request

from utils.db import db_session
from utils.models import ConversationState, User


FLOW = "onboard"


def _get_state(phone: str) -> Optional[ConversationState]:
    with db_session() as s:
        st = (
            s.query(ConversationState)
            .filter(ConversationState.phone == phone, ConversationState.flow == FLOW)
            .first()
        )
        return st


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
    _set_state(phone, "ask_name", {})
    return "Welcome! Let's get you set up. What's your full name?"


def handle_sms(phone: str, body: str) -> Optional[str]:
    st = _get_state(phone)
    if not st:
        return None
    data = json.loads(st.data or "{}")
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
        with db_session() as s:
            u = s.query(User).filter(User.phone == phone).first()
            if u:
                u.name = data.get("name")
                u.prison_id = data.get("prison_id")
                u.affiliate_code = code
            else:
                s.add(
                    User(
                        phone=phone,
                        name=data.get("name"),
                        prison_id=data.get("prison_id"),
                        affiliate_code=code,
                    )
                )
        _clear_state(phone)
        return "All set! Reply 'pay' to get a billing link or say 'pay' on a call to pay by phone."
    return None


def voice_prompt(step: str) -> str:
    if step == "ask_name":
        return "Welcome! Let's get you set up. Please say your full name after the tone."
    if step == "ask_prison_id":
        return "Thanks. Please say your prison I D."
    if step == "ask_affiliate":
        return "If you have an affiliate referral code, say it now. Otherwise say none."
    return ""


def handle_voice_input(phone: str, speech: str) -> str:
    st = _get_state(phone)
    if not st:
        _set_state(phone, "ask_name", {})
        return voice_prompt("ask_name")
    data = json.loads(st.data or "{}")
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
            u = s.query(User).filter(User.phone == phone).first()
            if u:
                u.name = data.get("name")
                u.prison_id = data.get("prison_id")
                u.affiliate_code = code
            else:
                s.add(
                    User(
                        phone=phone,
                        name=data.get("name"),
                        prison_id=data.get("prison_id"),
                        affiliate_code=code,
                    )
                )
        _clear_state(phone)
        return "You're all set up. To pay now, say pay, or you can text pay for a link."
    return ""

