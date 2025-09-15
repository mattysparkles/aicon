#!/usr/bin/env python3
import os
from datetime import datetime, timedelta

from utils.db import db_session
from utils.models import Interaction, Referral, User, Payment
from handlers.email import send_email


def main() -> None:
    since = datetime.utcnow() - timedelta(days=1)
    with db_session() as s:
        interactions = s.query(Interaction).filter(Interaction.created_at >= since).count()
        users = s.query(User).filter(User.created_at >= since).count()
        referrals = s.query(Referral).filter(Referral.created_at >= since).count()
        payments = s.query(Payment).filter(Payment.created_at >= since).count()

    body = (
        f"Daily Summary (last 24h)\n"
        f"Interactions: {interactions}\n"
        f"New Users: {users}\n"
        f"New Referrals: {referrals}\n"
        f"Payments: {payments}\n"
    )
    admins = os.environ.get("ADMIN_EMAILS", "").split(",")
    for a in admins:
        a = a.strip()
        if a:
            send_email(a, "AICon Daily Summary", body)


if __name__ == "__main__":
    main()

