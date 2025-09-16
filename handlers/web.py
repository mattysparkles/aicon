from __future__ import annotations

from flask import Blueprint, render_template
from flask import request, redirect, jsonify, session, send_file, url_for
from flask import json as fjson

from utils.db import db_session
from utils.models import User, Subscription, Interaction, UserPreference, Affiliate, Referral, Payout
from handlers.billing import checkout_link as checkout_link_view
from handlers.email import send_email
from handlers.voices import list_voice_keywords, set_user_voice_keyword
from handlers import security as security_handlers

import csv
import io
import os
from datetime import datetime
import json as pyjson
import secrets

bp = Blueprint("web", __name__)


@bp.get("/")
def landing():
    return render_template("landing.html")


@bp.get("/dashboard")
def dashboard():
    phone = session.get("user_id")
    if not phone:
        return redirect(url_for("web.login_get"))

    # Aggregate usage
    with db_session() as s:
        sms_count = s.query(Interaction).filter(Interaction.user_id == phone, Interaction.input_type == "sms").count()
        voice_count = s.query(Interaction).filter(Interaction.user_id == phone, Interaction.input_type == "voice").count()
        last5 = (
            s.query(Interaction)
            .filter(Interaction.user_id == phone)
            .order_by(Interaction.created_at.desc())
            .limit(5)
            .all()
        )
        sub = s.query(Subscription).filter(Subscription.phone == phone).order_by(Subscription.created_at.desc()).first()
        # Preferences
        prefs = {p.key: p.value for p in s.query(UserPreference).filter(UserPreference.user_id == phone).all()}

    approx_seconds_per_voice = int(os.environ.get("APPROX_SECONDS_PER_VOICE", "30"))
    minutes_used = round((voice_count * approx_seconds_per_voice) / 60.0, 1)

    return render_template(
        "dashboard.html",
        phone=phone,
        minutes_used=minutes_used,
        texts_sent=sms_count,
        current_plan=getattr(sub, "plan", None),
        billing_provider=getattr(sub, "provider", None),
        billing_since=getattr(sub, "created_at", None),
        last5=last5,
        voice_keywords=list_voice_keywords(),
        prefs=prefs,
    )


@bp.get("/login")
def login_get():
    return render_template("login.html")


@bp.post("/login")
def login_post():
    phone = (request.form.get("phone") or "").strip()
    phrase = (request.form.get("phrase") or "").strip()
    if not phone:
        return render_template("login.html", error="Phone number required.")
    # If a phrase is provided, verify it; otherwise allow soft login
    if phrase:
        ok = security_handlers.verify_phrase(phone, phrase)
        if not ok:
            return render_template("login.html", error="Invalid passphrase for this phone.")
    session["user_id"] = phone
    return redirect(url_for("web.dashboard"))


@bp.post("/logout")
def logout_post():
    session.pop("user_id", None)
    return redirect(url_for("web.login_get"))


@bp.post("/dashboard/preferences")
def dashboard_prefs_post():
    phone = session.get("user_id")
    if not phone:
        return redirect(url_for("web.login_get"))

    voice = (request.form.get("voice") or "").strip()
    persona = (request.form.get("persona") or "").strip()
    wallet = (request.form.get("wallet") or "").strip()
    billing_email = (request.form.get("billing_email") or "").strip()

    # Handle voice keyword
    if voice:
        try:
            set_user_voice_keyword(phone, voice)
        except Exception:
            pass

    # Persist preferences
    with db_session() as s:
        for key, value in (("persona", persona), ("wallet", wallet), ("billing_email", billing_email)):
            if not value:
                continue
            pref = (
                s.query(UserPreference)
                .filter(UserPreference.user_id == phone, UserPreference.key == key)
                .first()
            )
            if pref:
                pref.value = value
            else:
                s.add(UserPreference(user_id=phone, key=key, value=value))

    # Handle voice clone upload (placeholder store only)
    f = request.files.get("voice_clone")
    if f and f.filename:
        safe_name = f.filename.replace("..", ".").replace("/", "_")
        out_dir = os.path.join("static", "audio", "voice_clones", phone.strip("+"))
        os.makedirs(out_dir, exist_ok=True)
        f.save(os.path.join(out_dir, safe_name))

    return redirect(url_for("web.dashboard"))


@bp.post("/dashboard/passphrase")
def dashboard_passphrase_post():
    phone = session.get("user_id")
    if not phone:
        return redirect(url_for("web.login_get"))
    phrase = (request.form.get("phrase") or "").strip()
    if phrase:
        try:
            security_handlers.set_phrase(phone, phrase, method="text")
        except Exception:
            pass
    return redirect(url_for("web.dashboard"))


@bp.post("/dashboard/security")
def dashboard_security_post():
    phone = session.get("user_id")
    if not phone:
        return redirect(url_for("web.login_get"))
    pass_required = request.form.get("passphrase_required") == "on"
    twofa = request.form.get("twofa") == "on"

    with db_session() as s:
        for key, val in (("passphrase_required", "on" if pass_required else "off"), ("twofa", "on" if twofa else "off")):
            pref = (
                s.query(UserPreference)
                .filter(UserPreference.user_id == phone, UserPreference.key == key)
                .first()
            )
            if pref:
                pref.value = val
            else:
                s.add(UserPreference(user_id=phone, key=key, value=val))
    # Optional: accept a voiceprint upload placeholder
    f = request.files.get("voiceprint")
    if f and f.filename:
        safe_name = f.filename.replace("..", ".").replace("/", "_")
        out_dir = os.path.join("static", "audio", "voiceprints", phone.strip("+"))
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, safe_name)
        f.save(path)
        with db_session() as s:
            pref = (
                s.query(UserPreference)
                .filter(UserPreference.user_id == phone, UserPreference.key == "voiceprint_path")
                .first()
            )
            if pref:
                pref.value = path
            else:
                s.add(UserPreference(user_id=phone, key="voiceprint_path", value=path))
    return redirect(url_for("web.dashboard"))


@bp.get("/dashboard/export/conversations.csv")
def export_conversations_csv():
    phone = session.get("user_id")
    if not phone:
        return redirect(url_for("web.login_get"))
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["created_at", "type", "user_text", "response", "model", "voice_id"])
    with db_session() as s:
        rows = (
            s.query(Interaction)
            .filter(Interaction.user_id == phone)
            .order_by(Interaction.created_at.asc())
            .all()
        )
        for r in rows:
            writer.writerow([
                r.created_at.isoformat() if r.created_at else "",
                r.input_type,
                (r.transcript or "").replace("\n", " "),
                (r.response or "").replace("\n", " "),
                r.model or "",
                r.voice_id or "",
            ])
    mem = io.BytesIO(output.getvalue().encode("utf-8"))
    filename = f"conversations_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    return send_file(mem, mimetype="text/csv", as_attachment=True, download_name=filename)


@bp.get("/dashboard/export/voice_history.json")
def export_voice_history():
    phone = session.get("user_id")
    if not phone:
        return redirect(url_for("web.login_get"))
    data = []
    with db_session() as s:
        rows = (
            s.query(Interaction)
            .filter(Interaction.user_id == phone, Interaction.input_type == "voice")
            .order_by(Interaction.created_at.asc())
            .all()
        )
        for r in rows:
            data.append(
                {
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "transcript": r.transcript,
                    "response": r.response,
                    "voice_id": r.voice_id,
                }
            )
    blob = io.BytesIO()
    blob.write(pyjson.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
    blob.seek(0)
    filename = f"voice_history_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    return send_file(blob, mimetype="application/json", as_attachment=True, download_name=filename)


@bp.get("/affiliate")
def affiliate():
    phone = session.get("user_id")
    if not phone:
        return redirect(url_for("web.login_get"))
    with db_session() as s:
        aff = s.query(Affiliate).filter(Affiliate.owner_phone == phone).first()
        prefs = {p.key: p.value for p in s.query(UserPreference).filter(UserPreference.user_id == phone).all()}
        code = aff.code if aff else None
        # Signup tracking
        signups_total = s.query(User).filter(User.affiliate_code == code).count() if code else 0
        # Earnings
        aff_id = aff.id if aff else None
        earnings_total = 0
        earnings_week = 0
        earnings_day = 0
        recent_signups = []
        if aff_id:
            from sqlalchemy import func
            rows = s.query(func.sum(Referral.commission_cents)).filter(Referral.affiliate_id == aff_id).all()
            earnings_total = int(rows[0][0] or 0)
            # time windows
            from datetime import datetime, timedelta
            now = datetime.utcnow()
            start_week = now - timedelta(days=7)
            start_day = now - timedelta(days=1)
            earnings_week = int((s.query(func.sum(Referral.commission_cents)).filter(Referral.affiliate_id == aff_id, Referral.created_at >= start_week).scalar() or 0))
            earnings_day = int((s.query(func.sum(Referral.commission_cents)).filter(Referral.affiliate_id == aff_id, Referral.created_at >= start_day).scalar() or 0))
            # recent signups via Users table
            recent_signups = (
                s.query(User)
                .filter(User.affiliate_code == code)
                .order_by(User.created_at.desc())
                .limit(10)
                .all()
            )
        # Voiceprint verification status
        voice_verified = bool(prefs.get("voiceprint_path"))
        # Parent affiliate code (override)
        parent_code = prefs.get("parent_affiliate_code")
        # Payouts and balance
        payouts = []
        available_cents = 0
        if aff_id:
            payouts = s.query(Payout).filter(Payout.affiliate_id == aff_id).order_by(Payout.created_at.desc()).all()
            paid_out = int(sum(p.amount_cents for p in payouts if p.status in ("approved", "paid")))
            available_cents = max(0, earnings_total - paid_out)
        # Tier summary
        tier = None
        try:
            from handlers.billing import get_affiliate_tier_summary
            tier = get_affiliate_tier_summary(code or "") if code else None
        except Exception:
            tier = None
    base = request.url_root.rstrip("/")
    link = f"{base}/r/{code}" if code else None
    return render_template(
        "affiliate.html",
        code=code,
        link=link,
        prefs=prefs,
        earnings_total=earnings_total,
        earnings_week=earnings_week,
        earnings_day=earnings_day,
        signups_total=signups_total,
        recent_signups=recent_signups,
        payouts=payouts,
        available_cents=available_cents,
        min_withdraw_cents=1000,
        voice_verified=voice_verified,
        parent_code=parent_code,
        tier=tier,
    )


@bp.get("/r/<code>")
def affiliate_redirect(code: str):
    # Store code in session for prefill and redirect to signup
    session["affiliate_code"] = code
    return redirect(url_for("web.signup_get", affiliate=code))


@bp.post("/affiliate/generate")
def affiliate_generate():
    phone = session.get("user_id")
    if not phone:
        return redirect(url_for("web.login_get"))
    with db_session() as s:
        aff = s.query(Affiliate).filter(Affiliate.owner_phone == phone).first()
        if aff and aff.code:
            return redirect(url_for("web.affiliate"))
        # Generate unique code
        code = secrets.token_urlsafe(6).replace("_", "").replace("-", "").lower()
        s.add(Affiliate(code=code, owner_phone=phone, commission_rate_bps=500))
    return redirect(url_for("web.affiliate"))


@bp.post("/affiliate/set_parent")
def affiliate_set_parent():
    phone = session.get("user_id")
    if not phone:
        return redirect(url_for("web.login_get"))
    code = (request.form.get("parent_code") or "").strip()
    if not code:
        return redirect(url_for("web.affiliate"))
    with db_session() as s:
        parent = s.query(Affiliate).filter(Affiliate.code == code).first()
        mine = s.query(Affiliate).filter(Affiliate.owner_phone == phone).first()
        if not parent or (mine and mine.code == code):
            return redirect(url_for("web.affiliate"))
        pref = s.query(UserPreference).filter(UserPreference.user_id == phone, UserPreference.key == "parent_affiliate_code").first()
        if pref:
            pref.value = code
        else:
            s.add(UserPreference(user_id=phone, key="parent_affiliate_code", value=code))
    return redirect(url_for("web.affiliate"))


@bp.post("/affiliate/prefs")
def affiliate_prefs():
    phone = session.get("user_id")
    if not phone:
        return redirect(url_for("web.login_get"))
    method = (request.form.get("payout_method") or "").strip().lower()
    paypal_email = (request.form.get("paypal_email") or "").strip()
    crypto_wallet = (request.form.get("crypto_wallet") or "").strip()
    proxy_name = (request.form.get("proxy_name") or "").strip()
    proxy_email = (request.form.get("proxy_email") or "").strip()
    proxy_phone = (request.form.get("proxy_phone") or "").strip()
    invest = (request.form.get("invest_destination") or "none").strip().lower()
    vault_mode = request.form.get("vault_mode") == "on"
    vault_release = (request.form.get("vault_release") or "").strip()
    with db_session() as s:
        for k, v in (
            ("payout_method", method),
            ("paypal_email", paypal_email),
            ("crypto_wallet", crypto_wallet),
            ("proxy_name", proxy_name),
            ("proxy_email", proxy_email),
            ("proxy_phone", proxy_phone),
            ("invest_destination", invest),
            ("vault_mode", "on" if vault_mode else "off"),
            ("vault_release", vault_release),
        ):
            if v is None:
                continue
            pref = s.query(UserPreference).filter(UserPreference.user_id == phone, UserPreference.key == k).first()
            if pref:
                pref.value = v
            else:
                s.add(UserPreference(user_id=phone, key=k, value=v))
    return redirect(url_for("web.affiliate"))


@bp.post("/affiliate/withdraw")
def affiliate_withdraw():
    phone = session.get("user_id")
    if not phone:
        return redirect(url_for("web.login_get"))
    amount_cents = int(float(request.form.get("amount") or 0) * 100)
    with db_session() as s:
        aff = s.query(Affiliate).filter(Affiliate.owner_phone == phone).first()
        if not aff:
            return redirect(url_for("web.affiliate"))
        # Compute available
        from sqlalchemy import func
        total = int((s.query(func.sum(Referral.commission_cents)).filter(Referral.affiliate_id == aff.id).scalar() or 0))
        paid_out = int(sum(p.amount_cents for p in s.query(Payout).filter(Payout.affiliate_id == aff.id, Payout.status.in_(["approved", "paid"])) ))
        available = max(0, total - paid_out)
        if amount_cents <= 0 or amount_cents > available:
            amount_cents = available
        if amount_cents < 1000:
            return redirect(url_for("web.affiliate"))
        # Store wallet from prefs if crypto selected, plus custodian and investment/vault
        prefs = {p.key: p.value for p in s.query(UserPreference).filter(UserPreference.user_id == phone).all()}
        # Enforce vault lock: deny withdraw when vault mode on and release date in future
        if prefs.get("vault_mode") == "on" and prefs.get("vault_release"):
            try:
                rel = datetime.fromisoformat(prefs["vault_release"]).date()
                if rel > datetime.utcnow().date():
                    return redirect(url_for("web.affiliate"))
            except Exception:
                pass
        wallet = prefs.get("crypto_wallet") if (prefs.get("payout_method") == "crypto") else None
        release_at = None
        if prefs.get("vault_mode") == "on" and prefs.get("vault_release"):
            try:
                release_at = datetime.fromisoformat(prefs["vault_release"])  # YYYY-MM-DD
            except Exception:
                release_at = None
        s.add(Payout(
            affiliate_id=aff.id,
            amount_cents=amount_cents,
            wallet_address=wallet,
            status="pending",
            method=prefs.get("payout_method"),
            custodian_name=prefs.get("proxy_name"),
            custodian_email=prefs.get("proxy_email"),
            custodian_phone=prefs.get("proxy_phone"),
            vault_mode=("true" if prefs.get("vault_mode") == "on" else "false"),
            release_at=release_at,
            invest_destination=prefs.get("invest_destination", "none"),
        ))
    return redirect(url_for("web.affiliate"))


@bp.get("/affiliate/earnings.csv")
def affiliate_earnings_export():
    phone = session.get("user_id")
    if not phone:
        return redirect(url_for("web.login_get"))
    output = io.StringIO()
    w = csv.writer(output)
    # Disclaimer header
    w.writerow(["Crypto markets carry risk. Values may fluctuate. Invest at your own pace."])
    w.writerow(["date", "type", "amount_usd", "details"])
    with db_session() as s:
        aff = s.query(Affiliate).filter(Affiliate.owner_phone == phone).first()
        if not aff:
            mem = io.BytesIO(output.getvalue().encode("utf-8"))
            return send_file(mem, mimetype="text/csv", as_attachment=True, download_name="earnings.csv")
        # Commissions
        rows = s.query(Referral).filter(Referral.affiliate_id == aff.id).order_by(Referral.created_at.asc()).all()
        for r in rows:
            w.writerow([r.created_at.isoformat() if r.created_at else "", "commission", f"{(r.commission_cents or 0)/100:.2f}", f"referred={r.referred_phone}"])
        # Payouts
        pays = s.query(Payout).filter(Payout.affiliate_id == aff.id).order_by(Payout.created_at.asc()).all()
        for p in pays:
            w.writerow([p.created_at.isoformat() if p.created_at else "", f"payout:{p.status}", f"-{(p.amount_cents or 0)/100:.2f}", f"method={p.method or ''}; tx={p.tx_id or ''}"])
    mem = io.BytesIO(output.getvalue().encode("utf-8"))
    return send_file(mem, mimetype="text/csv", as_attachment=True, download_name="earnings.csv")


@bp.get("/affiliates/leaderboard")
def affiliates_leaderboard():
    facility = request.args.get("facility")
    from sqlalchemy import func
    results = []
    with db_session() as s:
        qry = s.query(Referral.affiliate_id, func.sum(Referral.commission_cents).label("commission")).group_by(Referral.affiliate_id)
        if facility:
            # Join via referred user
            ur = s.query(User.phone, User.facility_code).subquery()
            # Note: SQLAlchemy core join in subquery style; filter by facility by correlating via referred_phone
            rows = (
                s.query(Referral.affiliate_id, func.sum(Referral.commission_cents).label("commission"))
                .join(ur, ur.c.phone == Referral.referred_phone)
                .filter(ur.c.facility_code == facility)
                .group_by(Referral.affiliate_id)
                .order_by(func.sum(Referral.commission_cents).desc())
                .limit(50)
                .all()
            )
        else:
            rows = (
                qry.order_by(func.sum(Referral.commission_cents).desc()).limit(50).all()
            )
        for aff_id, commission in rows:
            aff = s.query(Affiliate).filter(Affiliate.id == aff_id).first()
            results.append({"affiliate_id": aff_id, "code": getattr(aff, "code", None), "commission_cents": int(commission or 0)})
    return jsonify(results)


@bp.get("/affiliate/flyer")
def affiliate_flyer():
    phone = session.get("user_id")
    if not phone:
        return redirect(url_for("web.login_get"))
    with db_session() as s:
        aff = s.query(Affiliate).filter(Affiliate.owner_phone == phone).first()
    base = request.url_root.rstrip("/")
    code = aff.code if aff else None
    link = f"{base}/r/{code}" if code else None
    return render_template("affiliate_flyer.html", code=code, link=link)


@bp.get("/affiliate/welcome-kit")
def affiliate_welcome():
    phone = session.get("user_id")
    if not phone:
        return redirect(url_for("web.login_get"))
    with db_session() as s:
        aff = s.query(Affiliate).filter(Affiliate.owner_phone == phone).first()
    base = request.url_root.rstrip("/")
    code = aff.code if aff else None
    link = f"{base}/r/{code}" if code else None
    return render_template("affiliate_welcome_kit.html", code=code, link=link)


@bp.get("/admin-ui")
def admin_ui():
    return render_template("admin.html")


@bp.get("/docs/offline")
def docs_offline():
    return render_template("docs_offline.html")


@bp.get("/signup")
def signup_get():
    affiliate = request.args.get("affiliate") or session.get("affiliate_code")
    return render_template("signup.html", affiliate_code=affiliate)


@bp.post("/signup")
def signup_post():
    phone = (request.form.get("phone") or "").strip()
    name = (request.form.get("name") or "").strip()
    prison_id = (request.form.get("prison_id") or "").strip()
    affiliate_code = (request.form.get("affiliate_code") or "").strip()
    plan = (request.form.get("plan") or "text").strip().lower()
    crypto = (request.form.get("crypto") == "on")

    if not phone:
        return render_template("signup.html", error="Phone number is required.")

    with db_session() as s:
        u = s.query(User).filter(User.phone == phone).first()
        if u:
            u.name = name or u.name
            u.prison_id = prison_id or u.prison_id
            u.affiliate_code = affiliate_code or u.affiliate_code
        else:
            s.add(User(phone=phone, name=name, prison_id=prison_id, affiliate_code=affiliate_code))
        # $10 signup bonus (once per referred phone per affiliate)
        if affiliate_code:
            aff = s.query(Affiliate).filter(Affiliate.code == affiliate_code).first()
            if aff:
                existing = (
                    s.query(Referral)
                    .filter(Referral.affiliate_id == aff.id, Referral.referred_phone == phone)
                    .first()
                )
                if not existing:
                    s.add(Referral(affiliate_id=aff.id, referred_phone=phone, amount_cents=0, commission_cents=1000))

    # Create checkout session via existing billing endpoint
    with request.environ.get('werkzeug.server.shutdown', None) or request.context:  # no-op, ensure context
        with bp.app.test_request_context():
            request.json = {  # type: ignore
                "phone": phone,
                "plan": plan,
                "crypto": crypto,
                "affiliate_code": affiliate_code,
            }
            res = checkout_link_view()
            data = fjson.loads(res.get_data(as_text=True))
            url = data.get("url")
            if url:
                return redirect(url)

    return render_template("signup.html", error="Could not generate checkout link. Please try again.")


@bp.get("/legal/tos")
def legal_tos():
    return render_template("legal_tos.html")


@bp.get("/legal/privacy")
def legal_privacy():
    return render_template("legal_privacy.html")


@bp.post("/contact")
def contact_submit():
    name = (request.form.get("name") or "").strip()
    email = (request.form.get("email") or "").strip()
    message = (request.form.get("message") or "").strip()
    if not message:
        return jsonify({"ok": False, "error": "Message required"}), 400
    subject = f"Website Contact: {name or 'Anonymous'}"
    content = f"From: {name} <{email}>\n\n{message}"
    try:
        # Send to site admin email from env EMAIL_TO
        import os
        to_addr = os.environ.get("EMAIL_TO") or os.environ.get("ADMIN_EMAILS") or email
        send_email(str(to_addr), subject, content)
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
