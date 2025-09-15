from __future__ import annotations

from flask import Blueprint, render_template
from flask import request, redirect
from flask import json as fjson

from utils.db import db_session
from utils.models import User
from handlers.billing import checkout_link as checkout_link_view

bp = Blueprint("web", __name__)


@bp.get("/")
def landing():
    return render_template("landing.html")


@bp.get("/dashboard")
def dashboard():
    return render_template("dashboard.html")


@bp.get("/affiliate")
def affiliate():
    return render_template("affiliate.html")


@bp.get("/admin-ui")
def admin_ui():
    return render_template("admin.html")


@bp.get("/docs/offline")
def docs_offline():
    return render_template("docs_offline.html")


@bp.get("/signup")
def signup_get():
    return render_template("signup.html")


@bp.post("/signup")
def signup_post():
    phone = (request.form.get("phone") or "").strip()
    name = (request.form.get("name") or "").strip()
    prison_id = (request.form.get("prison_id") or "").strip()
    affiliate_code = (request.form.get("affiliate_code") or "").strip()
    plan = (request.form.get("plan") or "basic").strip().lower()
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
