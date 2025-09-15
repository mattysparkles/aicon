"""Minimal admin endpoints to inspect interactions and preferences."""

from __future__ import annotations

import os
from flask import Response, jsonify, request

from utils.db import db_session
from utils.models import Interaction, UserPreference
from utils.models import User, Affiliate, Referral, Subscription, Payment


def _authorized() -> bool:
    token = os.environ.get("ADMIN_TOKEN")
    if not token:
        return True  # open if no token configured
    return request.args.get("token") == token


def init_app(app) -> None:
    @app.get("/admin/interactions")
    def admin_interactions() -> Response:
        if not _authorized():
            return Response("Unauthorized", status=401)
        limit = int(request.args.get("limit", "50"))
        limit = max(1, min(limit, 500))
        with db_session() as s:
            rows = (
                s.query(Interaction)
                .order_by(Interaction.created_at.desc())
                .limit(limit)
                .all()
            )
            data = [
                {
                    "id": r.id,
                    "user_id": r.user_id,
                    "input_type": r.input_type,
                    "transcript": r.transcript,
                    "response": r.response,
                    "model": r.model,
                    "voice_id": r.voice_id,
                    "created_at": r.created_at.isoformat(),
                }
                for r in rows
            ]
        return jsonify(data)

    @app.get("/admin/preferences")
    def admin_preferences() -> Response:
        if not _authorized():
            return Response("Unauthorized", status=401)
        with db_session() as s:
            rows = s.query(UserPreference).all()
            data = [
                {
                    "id": r.id,
                    "user_id": r.user_id,
                    "key": r.key,
                    "value": r.value,
                    "updated_at": r.updated_at.isoformat(),
                }
                for r in rows
            ]
        return jsonify(data)

    @app.get("/admin/users")
    def admin_users() -> Response:
        if not _authorized():
            return Response("Unauthorized", status=401)
        with db_session() as s:
            rows = s.query(User).all()
            data = [
                {
                    "id": r.id,
                    "phone": r.phone,
                    "name": r.name,
                    "prison_id": r.prison_id,
                    "affiliate_code": r.affiliate_code,
                    "created_at": r.created_at.isoformat(),
                }
                for r in rows
            ]
        return jsonify(data)

    @app.get("/admin/affiliates")
    def admin_affiliates() -> Response:
        if not _authorized():
            return Response("Unauthorized", status=401)
        with db_session() as s:
            rows = s.query(Affiliate).all()
            data = [
                {
                    "id": r.id,
                    "code": r.code,
                    "owner_phone": r.owner_phone,
                    "commission_rate_bps": r.commission_rate_bps,
                    "created_at": r.created_at.isoformat(),
                }
                for r in rows
            ]
        return jsonify(data)

    @app.get("/admin/referrals")
    def admin_referrals() -> Response:
        if not _authorized():
            return Response("Unauthorized", status=401)
        with db_session() as s:
            rows = s.query(Referral).all()
            data = [
                {
                    "id": r.id,
                    "affiliate_id": r.affiliate_id,
                    "referred_phone": r.referred_phone,
                    "amount_cents": r.amount_cents,
                    "commission_cents": r.commission_cents,
                    "created_at": r.created_at.isoformat(),
                }
                for r in rows
            ]
        return jsonify(data)

    @app.get("/admin/subscriptions")
    def admin_subscriptions() -> Response:
        if not _authorized():
            return Response("Unauthorized", status=401)
        with db_session() as s:
            rows = s.query(Subscription).all()
            data = [
                {
                    "id": r.id,
                    "phone": r.phone,
                    "plan": r.plan,
                    "provider": r.provider,
                    "status": r.status,
                    "stripe_sub_id": r.stripe_sub_id,
                    "created_at": r.created_at.isoformat(),
                }
                for r in rows
            ]
        return jsonify(data)

    @app.get("/admin/payments")
    def admin_payments() -> Response:
        if not _authorized():
            return Response("Unauthorized", status=401)
        with db_session() as s:
            rows = s.query(Payment).all()
            data = [
                {
                    "id": r.id,
                    "phone": r.phone,
                    "amount_cents": r.amount_cents,
                    "currency": r.currency,
                    "method": r.method,
                    "status": r.status,
                    "provider_payment_id": r.provider_payment_id,
                    "created_at": r.created_at.isoformat(),
                }
                for r in rows
            ]
        return jsonify(data)
