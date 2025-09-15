from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import declarative_base


Base = declarative_base()


class Interaction(Base):
    __tablename__ = "interactions"

    id = Column(Integer, primary_key=True)
    user_id = Column(String(64), index=True, nullable=False)  # phone number
    input_type = Column(String(16), nullable=False)  # sms|voice
    transcript = Column(Text, nullable=True)
    response = Column(Text, nullable=True)
    model = Column(String(64), nullable=True)
    voice_id = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class UserPreference(Base):
    __tablename__ = "user_preferences"

    id = Column(Integer, primary_key=True)
    user_id = Column(String(64), nullable=False)  # phone number
    key = Column(String(64), nullable=False)
    value = Column(String(256), nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_user_pref_key"),
    )


# --- Phase 2: Billing & Accounts ---

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    phone = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(120), nullable=True)
    prison_id = Column(String(120), nullable=True)
    affiliate_code = Column(String(64), nullable=True)  # code they used to sign up
    assigned_number = Column(String(64), nullable=True, unique=True)  # per-user Twilio number
    facility_code = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Affiliate(Base):
    __tablename__ = "affiliates"

    id = Column(Integer, primary_key=True)
    code = Column(String(64), unique=True, nullable=False, index=True)
    owner_phone = Column(String(64), nullable=False)  # the referrer (user's phone)
    commission_rate_bps = Column(Integer, default=1000, nullable=False)  # 10% default
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Referral(Base):
    __tablename__ = "referrals"

    id = Column(Integer, primary_key=True)
    affiliate_id = Column(Integer, nullable=False)
    referred_phone = Column(String(64), nullable=False)
    # Monetary tracking (store as strings or minor units if needed; simple text for now)
    amount_cents = Column(Integer, default=0, nullable=False)
    commission_cents = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True)
    phone = Column(String(64), index=True, nullable=False)
    plan = Column(String(64), nullable=False)  # basic|pro|custom
    provider = Column(String(32), default="stripe", nullable=False)  # stripe|crypto
    status = Column(String(32), default="active", nullable=False)
    stripe_sub_id = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True)
    phone = Column(String(64), index=True, nullable=False)
    amount_cents = Column(Integer, nullable=False)
    currency = Column(String(8), default="usd", nullable=False)
    method = Column(String(32), nullable=False)  # stripe|twilio_pay|crypto
    status = Column(String(32), default="succeeded", nullable=False)
    provider_payment_id = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Plan(Base):
    __tablename__ = "plans"

    id = Column(Integer, primary_key=True)
    name = Column(String(64), unique=True, nullable=False)
    price_id = Column(String(128), nullable=True)  # Stripe price ID
    description = Column(Text, nullable=True)
    active = Column(String(8), default="true", nullable=False)  # "true" or "false"
    discount_coupon = Column(String(64), nullable=True)  # Stripe coupon id/code
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Payout(Base):
    __tablename__ = "payouts"

    id = Column(Integer, primary_key=True)
    affiliate_id = Column(Integer, nullable=False)
    amount_cents = Column(Integer, nullable=False)
    wallet_address = Column(String(256), nullable=True)
    status = Column(String(32), default="pending", nullable=False)  # pending|approved|paid|rejected
    method = Column(String(32), nullable=True)  # paypal|crypto|commissary
    tx_id = Column(String(128), nullable=True)
    custodian_name = Column(String(128), nullable=True)
    custodian_email = Column(String(128), nullable=True)
    custodian_phone = Column(String(64), nullable=True)
    vault_mode = Column(String(8), default="false", nullable=False)  # "true" or "false"
    release_at = Column(DateTime, nullable=True)
    invest_destination = Column(String(32), nullable=True)  # none|robinhood|acorns
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ConversationState(Base):
    __tablename__ = "conversation_state"

    id = Column(Integer, primary_key=True)
    phone = Column(String(64), index=True, nullable=False)
    flow = Column(String(64), nullable=False)  # onboard|pay
    step = Column(String(64), nullable=False)
    data = Column(Text, nullable=True)  # JSON blob
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Feedback(Base):
    __tablename__ = "feedback"

    id = Column(Integer, primary_key=True)
    phone = Column(String(64), index=True, nullable=False)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SecurityPhrase(Base):
    __tablename__ = "security_phrases"

    id = Column(Integer, primary_key=True)
    phone = Column(String(64), index=True, nullable=False)
    method = Column(String(16), default="speech", nullable=False)  # speech|dtmf|text
    salt = Column(String(64), nullable=False)
    hash = Column(String(128), nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Facility(Base):
    __tablename__ = "facilities"

    id = Column(Integer, primary_key=True)
    code = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(128), nullable=True)
    price_basic = Column(String(64), nullable=True)  # Stripe price ID override
    price_pro = Column(String(64), nullable=True)
    allow_crypto_discount = Column(String(8), default="true")  # "true" or "false"
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class FlowEvent(Base):
    __tablename__ = "flow_events"

    id = Column(Integer, primary_key=True)
    phone = Column(String(64), index=True, nullable=False)
    event = Column(String(64), nullable=False)
    meta = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
