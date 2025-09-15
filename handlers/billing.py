"""Billing utilities: Stripe Checkout links and affiliate crediting."""

from __future__ import annotations

import os
import json
from typing import Optional

from flask import Blueprint, Response, jsonify, request

from utils.db import db_session
from utils.models import Payment, Referral, Affiliate, Facility, User

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

    Body params: {"phone":"+1...", "plan":"basic|pro", "crypto":bool, "affiliate_code": "ABC123"}
    If called from SMS, you can proxy this via server-side and reply with the URL.
    """
    data = request.get_json(silent=True) or {}
    phone = str(data.get("phone", "")).strip()
    plan = str(data.get("plan", "basic")).strip().lower()
    crypto = bool(data.get("crypto", False))
    affiliate_code = (data.get("affiliate_code") or "").strip()

    # Facility-specific overrides
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
    price_map = {"basic": price_basic, "pro": price_pro}
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


def credit_affiliate(phone: str, amount_cents: int, affiliate_code: Optional[str]) -> None:
    if not affiliate_code:
        return
    with db_session() as s:
        aff = s.query(Affiliate).filter(Affiliate.code == affiliate_code).first()
        if not aff:
            return
        commission_cents = amount_cents * aff.commission_rate_bps // 10000
        s.add(
            Referral(
                affiliate_id=aff.id,
                referred_phone=phone,
                amount_cents=amount_cents,
                commission_cents=commission_cents,
            )
        )


def init_app(app) -> None:
    app.register_blueprint(bp)
