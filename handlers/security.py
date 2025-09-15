from __future__ import annotations

import os
import hashlib
import secrets
from typing import Optional

from utils.db import db_session
from utils.models import SecurityPhrase


def _hash_phrase(phrase: str, salt: Optional[str] = None) -> tuple[str, str]:
    s = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", phrase.encode("utf-8"), bytes.fromhex(s), 200_000)
    return s, h.hex()


def set_phrase(phone: str, phrase: str, method: str = "speech") -> None:
    salt, hh = _hash_phrase(phrase)
    with db_session() as s:
        row = s.query(SecurityPhrase).filter(SecurityPhrase.phone == phone).first()
        if row:
            row.salt = salt
            row.hash = hh
            row.method = method
        else:
            s.add(SecurityPhrase(phone=phone, method=method, salt=salt, hash=hh))


def verify_phrase(phone: str, phrase: str) -> bool:
    with db_session() as s:
        row = s.query(SecurityPhrase).filter(SecurityPhrase.phone == phone).first()
        if not row:
            return False
        _, hh = _hash_phrase(phrase, salt=row.salt)
        return secrets.compare_digest(hh, row.hash)

