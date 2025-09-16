"""Admin dashboard: auth, CRUD panels, analytics, and logs."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from collections import defaultdict
from flask import Response, jsonify, request, session, redirect, url_for, render_template

from utils.db import db_session
from utils.models import Interaction, UserPreference
from utils.models import User, Affiliate, Referral, Subscription, Payment, Plan, Payout


def _authorized() -> bool:
    # Session-based admin or token fallback
    if session.get("is_admin"):
        return True
    token = os.environ.get("ADMIN_TOKEN")
    if not token:
        return False
    return request.args.get("token") == token


def init_app(app) -> None:
    @app.get("/admin/login")
    def admin_login_get():
        if session.get("is_admin"):
            return redirect(url_for("admin_home"))
        return render_template("admin_login.html")

    @app.post("/admin/login")
    def admin_login_post():
        email = (request.form.get("email") or "").strip().lower()
        password = (request.form.get("password") or "").strip()
        token = (request.form.get("token") or "").strip()
        ok = False
        # Env-based credentials
        admin_email = (os.environ.get("ADMIN_EMAIL") or "").strip().lower()
        admin_pw = os.environ.get("ADMIN_PASSWORD")
        admin_token = os.environ.get("ADMIN_TOKEN")
        if admin_email and admin_pw and email and password:
            if email == admin_email and password == admin_pw:
                ok = True
        if not ok and admin_token and token == admin_token:
            ok = True
        if ok:
            session["is_admin"] = True
            if email:
                session["admin_email"] = email
            return redirect(url_for("admin_home"))
        return render_template("admin_login.html", error="Invalid credentials")

    @app.post("/admin/logout")
    def admin_logout_post():
        session.pop("is_admin", None)
        return redirect(url_for("admin_login_get"))

    @app.get("/admin")
    def admin_home():  # type: ignore
        if not _authorized():
            return redirect(url_for("admin_login_get"))
        return render_template("admin.html")
    @app.get("/admin/interactions")
    def admin_interactions() -> Response:
        if not _authorized():
            return Response("Unauthorized", status=401)
        q = request.args.get("q") or ""
        limit = int(request.args.get("limit", "50"))
        limit = max(1, min(limit, 500))
        with db_session() as s:
            qry = s.query(Interaction)
            if q:
                like = f"%{q}%"
                from sqlalchemy import or_
                qry = qry.filter(or_(Interaction.transcript.like(like), Interaction.response.like(like), Interaction.user_id.like(like)))
            rows = qry.order_by(Interaction.created_at.desc()).limit(limit).all()
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
        q = request.args.get("q") or ""
        with db_session() as s:
            qry = s.query(User)
            if q:
                like = f"%{q}%"
                from sqlalchemy import or_
                qry = qry.filter(or_(User.phone.like(like), User.name.like(like)))
            rows = qry.all()
            data = [
                {
                    "id": r.id,
                    "phone": r.phone,
                    "name": r.name,
                    "prison_id": r.prison_id,
                    "affiliate_code": r.affiliate_code,
                    "suspended": (s.query(UserPreference).filter(UserPreference.user_id == r.phone, UserPreference.key == "suspended", UserPreference.value == "on").count() > 0),
                    "created_at": r.created_at.isoformat(),
                }
                for r in rows
            ]
        return jsonify(data)

    @app.post("/admin/users/update")
    def admin_users_update() -> Response:
        if not _authorized():
            return Response("Unauthorized", status=401)
        data = request.get_json(silent=True) or {}
        phone = str(data.get("phone") or "").strip()
        action = str(data.get("action") or "").strip()
        if not phone or not action:
            return jsonify({"ok": False, "error": "phone and action required"}), 400
        with db_session() as s:
            if action == "suspend":
                pref = s.query(UserPreference).filter(UserPreference.user_id == phone, UserPreference.key == "suspended").first()
                if pref:
                    pref.value = "on"
                else:
                    s.add(UserPreference(user_id=phone, key="suspended", value="on"))
            elif action == "unsuspend":
                pref = s.query(UserPreference).filter(UserPreference.user_id == phone, UserPreference.key == "suspended").first()
                if pref:
                    pref.value = "off"
            elif action == "set_affiliate_code":
                code = str(data.get("code") or "").strip()
                u = s.query(User).filter(User.phone == phone).first()
                if u:
                    u.affiliate_code = code or None
            elif action == "upgrade_plan":
                plan = str(data.get("plan") or "").strip().lower()
                sub = s.query(Subscription).filter(Subscription.phone == phone).order_by(Subscription.created_at.desc()).first()
                if sub:
                    sub.plan = plan
                    sub.status = "active"
                else:
                    s.add(Subscription(phone=phone, plan=plan, provider="admin", status="active"))
            else:
                return jsonify({"ok": False, "error": "unknown action"}), 400
        return jsonify({"ok": True})

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

    @app.get("/admin/affiliates/top")
    def admin_affiliates_top() -> Response:
        if not _authorized():
            return Response("Unauthorized", status=401)
        with db_session() as s:
            # Sum commissions by affiliate
            from sqlalchemy import func
            rows = (
                s.query(Referral.affiliate_id, func.sum(Referral.commission_cents).label("commission"))
                .group_by(Referral.affiliate_id)
                .order_by(func.sum(Referral.commission_cents).desc())
                .limit(20)
                .all()
            )
            # Map affiliate code
            result = []
            for aff_id, commission in rows:
                aff = s.query(Affiliate).filter(Affiliate.id == aff_id).first()
                result.append({"affiliate_id": aff_id, "code": getattr(aff, "code", None), "commission_cents": int(commission or 0)})
        return jsonify(result)

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

    @app.get("/admin/plans")
    def admin_plans() -> Response:
        if not _authorized():
            return Response("Unauthorized", status=401)
        with db_session() as s:
            rows = s.query(Plan).all()
            data = [
                {
                    "id": r.id,
                    "name": r.name,
                    "price_id": r.price_id,
                    "description": r.description,
                    "active": r.active,
                    "discount_coupon": r.discount_coupon,
                    "created_at": r.created_at.isoformat(),
                }
                for r in rows
            ]
        return jsonify(data)

    @app.post("/admin/plans")
    def admin_plans_create() -> Response:
        if not _authorized():
            return Response("Unauthorized", status=401)
        data = request.get_json(silent=True) or {}
        name = str(data.get("name") or "").strip()
        if not name:
            return jsonify({"ok": False, "error": "name required"}), 400
        with db_session() as s:
            s.add(Plan(name=name, price_id=data.get("price_id"), description=data.get("description"), active=("true" if (str(data.get("active", "true")).lower() != "false") else "false"), discount_coupon=data.get("discount_coupon")))
        return jsonify({"ok": True})

    @app.post("/admin/plans/update")
    def admin_plans_update() -> Response:
        if not _authorized():
            return Response("Unauthorized", status=401)
        data = request.get_json(silent=True) or {}
        pid = int(data.get("id")) if data.get("id") is not None else None
        if not pid:
            return jsonify({"ok": False, "error": "id required"}), 400
        with db_session() as s:
            p = s.query(Plan).filter(Plan.id == pid).first()
            if not p:
                return jsonify({"ok": False, "error": "not found"}), 404
            for k in ("name", "price_id", "description", "discount_coupon"):
                if k in data:
                    setattr(p, k, data.get(k))
            if "active" in data:
                p.active = "true" if str(data["active"]).lower() != "false" else "false"
        return jsonify({"ok": True})

    @app.post("/admin/plans/delete")
    def admin_plans_delete() -> Response:
        if not _authorized():
            return Response("Unauthorized", status=401)
        data = request.get_json(silent=True) or {}
        pid = int(data.get("id")) if data.get("id") is not None else None
        if not pid:
            return jsonify({"ok": False, "error": "id required"}), 400
        with db_session() as s:
            p = s.query(Plan).filter(Plan.id == pid).first()
            if p:
                s.delete(p)
        return jsonify({"ok": True})

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

    @app.get("/admin/payouts")
    def admin_payouts() -> Response:
        if not _authorized():
            return Response("Unauthorized", status=401)
        with db_session() as s:
            rows = s.query(Payout).order_by(Payout.created_at.desc()).all()
            data = [
                {
                    "id": r.id,
                    "affiliate_id": r.affiliate_id,
                    "amount_cents": r.amount_cents,
                    "wallet_address": r.wallet_address,
                    "status": r.status,
                    "created_at": r.created_at.isoformat(),
                }
                for r in rows
            ]
        return jsonify(data)

    @app.post("/admin/payouts/update")
    def admin_payouts_update() -> Response:
        if not _authorized():
            return Response("Unauthorized", status=401)
        data = request.get_json(silent=True) or {}
        pid = int(data.get("id") or 0)
        status = str(data.get("status") or "").strip()
        if not pid or status not in ("pending", "approved", "paid", "rejected"):
            return jsonify({"ok": False, "error": "invalid params"}), 400
        with db_session() as s:
            p = s.query(Payout).filter(Payout.id == pid).first()
            if not p:
                return jsonify({"ok": False, "error": "not found"}), 404
            p.status = status
            if "tx_id" in data:
                p.tx_id = str(data.get("tx_id") or "")
            if "method" in data:
                p.method = str(data.get("method") or "")
            if "notes" in data:
                p.notes = str(data.get("notes") or "")
        return jsonify({"ok": True})

    @app.get("/admin/analytics/summary")
    def admin_analytics_summary() -> Response:
        if not _authorized():
            return Response("Unauthorized", status=401)
        days = int(request.args.get("days", "30"))
        days = max(1, min(days, 365))
        since = datetime.utcnow() - timedelta(days=days)
        by_day = defaultdict(lambda: {"sms": 0, "voice": 0})
        subs_by_day = defaultdict(int)
        refs_by_day = defaultdict(int)
        hour_hist = [0] * 24
        heatmap = [[0 for _ in range(24)] for __ in range(7)]  # dow x hour
        with db_session() as s:
            rows = s.query(Interaction).filter(Interaction.created_at >= since).all()
            for r in rows:
                d = (r.created_at.date().isoformat() if r.created_at else "unknown")
                if r.input_type in ("sms", "voice"):
                    by_day[d][r.input_type] += 1
                if r.created_at:
                    hour_hist[r.created_at.hour] += 1
                    dow = r.created_at.weekday()  # 0=Mon
                    heatmap[dow][r.created_at.hour] += 1
            subs = s.query(Subscription).filter(Subscription.created_at >= since).all()
            for r in subs:
                d = (r.created_at.date().isoformat() if r.created_at else "unknown")
                subs_by_day[d] += 1
            refs = s.query(Referral).filter(Referral.created_at >= since).all()
            for r in refs:
                d = (r.created_at.date().isoformat() if r.created_at else "unknown")
                refs_by_day[d] += 1
        # Normalize labels
        labels = sorted(set(list(by_day.keys()) + list(subs_by_day.keys()) + list(refs_by_day.keys())))
        usage = {"labels": labels, "sms": [by_day[l]["sms"] for l in labels], "voice": [by_day[l]["voice"] for l in labels]}
        subs = {"labels": labels, "count": [subs_by_day[l] for l in labels]}
        refs = {"labels": labels, "count": [refs_by_day[l] for l in labels]}
        return jsonify({"usage": usage, "subscriptions": subs, "referrals": refs, "hour_hist": hour_hist, "heatmap": heatmap})
