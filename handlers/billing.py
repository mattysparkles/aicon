"""Billing utilities: Stripe Checkout links and affiliate crediting."""

from __future__ import annotations

import os
import json
from typing import Optional

from flask import Blueprint, Response, jsonify, request

from utils.db import db_session
from utils.models import Payment, Referral, Affiliate, Facility, User
from utils.models import UserPreference
from datetime import datetime, timedelta

bp = Blueprint("billing", __name__)


def _stripe():
    import stripe  # lazy import

    api_key = os.environ.get("STRIPE_API_KEY")
    if not api_key:
        raise RuntimeError("STRIPE_API_KEY not set")
    stripe.api_key = api_key
    return stripe


def _apply_crypto_discount(params: dict, crypto: bool) -> dict:
    if not crypto:
        return params
    coupon = os.environ.get("STRIPE_COUPON_CRYPTO")
    if coupon:
        params.setdefault("discounts", []).append({"coupon": coupon})
    return params


@bp.post("/billing/checkout_link")
def checkout_link() -> Response:
    """Create a Stripe Checkout session and return the URL.

    Body params: {"phone":"+1...", "plan":"text|basic_voice|premium_voice_plus|unlimited", "crypto":bool, "affiliate_code": "ABC123"}
    If called from SMS, you can proxy this via server-side and reply with the URL.
    """
    data = request.get_json(silent=True) or {}
    phone = str(data.get("phone", "")).strip()
    plan = str(data.get("plan", "basic")).strip().lower()
    crypto = bool(data.get("crypto", False))
    affiliate_code = (data.get("affiliate_code") or "").strip()

    # Price IDs for new plans (env-driven)
    price_text = os.environ.get("STRIPE_PRICE_TEXT")
    price_basic_voice = os.environ.get("STRIPE_PRICE_BASIC_VOICE")
    price_premium_voice = os.environ.get("STRIPE_PRICE_PREMIUM_VOICE")
    price_unlimited = os.environ.get("STRIPE_PRICE_UNLIMITED")

    # Facility-specific overrides (legacy basic/pro kept for backward compatibility)
    price_basic = os.environ.get("STRIPE_PRICE_BASIC")
    price_pro = os.environ.get("STRIPE_PRICE_PRO")
    allow_crypto = True
    with db_session() as s:
        if phone:
            u = s.query(User).filter(User.phone == phone).first()
            if u and u.facility_code:
                f = s.query(Facility).filter(Facility.code == u.facility_code).first()
                if f:
                    price_basic = f.price_basic or price_basic
                    price_pro = f.price_pro or price_pro
                    allow_crypto = (f.allow_crypto_discount or "true").lower() == "true"
    # Support both legacy and new keys; prefer new keys
    price_map = {
        "text": price_text,
        "basic_voice": price_basic_voice or price_basic,
        "premium_voice_plus": price_premium_voice or price_pro,
        "unlimited": price_unlimited,
        # legacy fallbacks
        "basic": price_basic,
        "pro": price_pro,
    }
    price_id = price_map.get(plan)
    if not price_id:
        return jsonify({"error": "unknown plan"}), 400

    stripe = _stripe()
    success_url = os.environ.get("STRIPE_SUCCESS_URL", "https://example.com/success")
    cancel_url = os.environ.get("STRIPE_CANCEL_URL", "https://example.com/cancel")

    params = {
        "mode": "subscription",
        "customer_email": None,
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": success_url + "?session_id={CHECKOUT_SESSION_ID}",
        "cancel_url": cancel_url,
        "metadata": {"phone": phone, "plan": plan, "affiliate_code": affiliate_code},
    }
    params = _apply_crypto_discount(params, crypto and allow_crypto)

    session = stripe.checkout.Session.create(**params)
    return jsonify({"url": session.url})


def _tiers_from_env() -> list[dict]:
    """Read tier config from AFFILIATE_TIERS_JSON or return sensible defaults.

    Format example:
    [
      {"min_signups": 0, "min_velocity_30d": 0, "percent_bps": 500, "months": 60},
      {"min_signups": 25, "min_velocity_30d": 10, "percent_bps": 700, "months": 72},
      {"min_signups": 100, "min_velocity_30d": 30, "percent_bps": 1000, "months": 84}
    ]
    """
    raw = os.environ.get("AFFILIATE_TIERS_JSON")
    if raw:
        try:
            tiers = json.loads(raw)
            return sorted(tiers, key=lambda t: (int(t.get("min_signups", 0)), int(t.get("min_velocity_30d", 0))))
        except Exception:
            pass
    # Defaults: base 5% for 60 months; scale up with signups/velocity
    return [
        {"min_signups": 0, "min_velocity_30d": 0, "percent_bps": 500, "months": 60},
        {"min_signups": 25, "min_velocity_30d": 10, "percent_bps": 700, "months": 72},
        {"min_signups": 100, "min_velocity_30d": 30, "percent_bps": 1000, "months": 84},
    ]


def _current_tier(s, aff: Affiliate) -> dict:
    from datetime import datetime, timedelta
    # Signups are users who used this affiliate code
    code = getattr(aff, "code", None)
    total_signups = 0
    velocity_30d = 0
    if code:
        total_signups = s.query(User).filter(User.affiliate_code == code).count()
        since = datetime.utcnow() - timedelta(days=30)
        velocity_30d = s.query(User).filter(User.affiliate_code == code, User.created_at >= since).count()
    tiers = _tiers_from_env()
    best = tiers[0]
    for t in tiers:
        if total_signups >= int(t.get("min_signups", 0)) or velocity_30d >= int(t.get("min_velocity_30d", 0)):
            best = t
    best = {
        "min_signups": int(best.get("min_signups", 0)),
        "min_velocity_30d": int(best.get("min_velocity_30d", 0)),
        "percent_bps": int(best.get("percent_bps", 500)),
        "months": int(best.get("months", 60)),
        "metrics": {"total_signups": total_signups, "velocity_30d": velocity_30d},
    }
    # Compute next milestone for UI
    next_t = None
    for t in _tiers_from_env():
        if t["min_signups"] > best["min_signups"] or t["min_velocity_30d"] > best["min_velocity_30d"]:
            next_t = t
            break
    best["next_tier"] = next_t
    return best


def get_affiliate_tier_summary(affiliate_code: str) -> Optional[dict]:
    """Convenience accessor for web layer to display current tier and next milestone.

    Returns a dict with keys: percent_bps, months, metrics{total_signups, velocity_30d}, next_tier{...}
    """
    if not affiliate_code:
        return None
    with db_session() as s:
        aff = s.query(Affiliate).filter(Affiliate.code == affiliate_code).first()
        if not aff:
            return None
        return _current_tier(s, aff)


def credit_affiliate(phone: str, amount_cents: int, affiliate_code: Optional[str]) -> None:
    """Credit commissions for a payment using user-based referrals.

    - Case-insensitive affiliate codes supported via users.affiliate_code.
    - If the paying user has a referrer_id, use that user as referrer.
    - Default payout is 5% (500 bps). If referrer has affiliate_rate_bps set, use it
      (for special accounts, seed to 1500 bps = 15%).
    - Update referrer.affiliate_balance_cents and total_earned_cents.
    """
    if amount_cents <= 0:
        return
    with db_session() as s:
        payer = s.query(User).filter(User.phone == phone).first()
        referrer = None
        # Prefer explicit referrer link
        if payer and getattr(payer, "referrer_id", None):
            referrer = s.query(User).filter(User.id == payer.referrer_id).first()
        # Fallback: look up by provided affiliate_code (case-insensitive)
        if not referrer and affiliate_code:
            code_lc = affiliate_code.lower()
            all_refs = s.query(User).filter(User.affiliate_code.isnot(None)).all()
            for u in all_refs:
                if (u.affiliate_code or "").lower() == code_lc:
                    referrer = u
                    break
        if not referrer:
            return
        rate_bps = int(getattr(referrer, "affiliate_rate_bps", 0) or 0) or 500
        commission_cents = int(round(amount_cents * (rate_bps / 10000.0)))
        referrer.affiliate_balance_cents = int(getattr(referrer, "affiliate_balance_cents", 0) or 0) + commission_cents
        referrer.total_earned_cents = int(getattr(referrer, "total_earned_cents", 0) or 0) + commission_cents


def init_app(app) -> None:
    app.register_blueprint(bp)


@bp.post("/billing/request_payout")
def request_payout() -> Response:
    """Allow users to request a crypto payout (BTC/ETH/DOGE/RVN/PEP/LTC/JEM)."""
    data = request.get_json(silent=True) or {}
    phone = str(data.get("phone", "")).strip()
    asset = str(data.get("asset", "")).strip().upper()
    address = str(data.get("address", "")).strip()
    amount_cents = int(data.get("amount_cents", 0))
    if asset not in {"BTC", "ETH", "DOGE", "RVN", "PEP", "LTC", "JEM"}:
        return jsonify({"error": "unsupported asset"}), 400
    if not phone or not address or amount_cents <= 0:
        return jsonify({"error": "missing phone, address, or amount"}), 400
    from utils.models import Payout
    with db_session() as s:
        u = s.query(User).filter(User.phone == phone).first()
        if not u:
            return jsonify({"error": "user not found"}), 404
        if (u.affiliate_balance_cents or 0) < amount_cents:
            return jsonify({"error": "insufficient affiliate balance"}), 400
        u.affiliate_balance_cents = (u.affiliate_balance_cents or 0) - amount_cents
        s.add(Payout(affiliate_id=u.id, amount_cents=amount_cents, wallet_address=address, status="pending", method="crypto", asset=asset))
    return jsonify({"status": "requested"})
