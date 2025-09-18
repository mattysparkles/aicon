from utils.db import db_session
from utils.models import User
import uuid

SPECIALS = [
    {
        "name": "Melony Collins",
        "phone": "+1-XXX-XXX-0001",
        "affiliate_code": "FELONYMELONY",
        "affiliate_rate_bps": 1500,  # 15%
        "usage_unlimited": "true",
    },
    {
        "name": "Joseph Sweeney",
        "phone": "+1-XXX-XXX-0002",
        "affiliate_code": "PRIORS19",
        "affiliate_rate_bps": 1500,  # 15%
        "usage_unlimited": "true",
    },
    {
        "name": "Melissa Miller",
        "phone": "+1-XXX-XXX-0003",
        "affiliate_code": "Melissdemeneaor33",
        "affiliate_rate_bps": 1500,  # 15%
        "usage_unlimited": "true",
    },
]


def seed():
    with db_session() as s:
        for spec in SPECIALS:
            u = s.query(User).filter(User.phone == spec["phone"]).first()
            if not u:
                u = User(phone=spec["phone"], name=spec["name"]) 
                s.add(u)
                s.flush()
            if not u.user_uuid:
                u.user_uuid = str(uuid.uuid4())
            u.affiliate_code = spec["affiliate_code"]
            u.affiliate_rate_bps = spec["affiliate_rate_bps"]
            u.usage_unlimited = spec["usage_unlimited"]
            if not u.phone_number:
                u.phone_number = u.phone
            if not u.memory_enabled:
                u.memory_enabled = "true"


if __name__ == "__main__":
    seed()
    print("Seeded special users.")

