"""SendGrid email sender."""

import os
from typing import Optional

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
# Accept either SENDGRID_FROM_EMAIL or EMAIL_FROM for compatibility
SENDGRID_FROM_EMAIL = os.environ.get("SENDGRID_FROM_EMAIL") or os.environ.get("EMAIL_FROM")


def send_email(to: str, subject: str, content: str) -> Optional[str]:
    """Send an email using SendGrid."""
    if not SENDGRID_API_KEY or not SENDGRID_FROM_EMAIL:
        raise RuntimeError("SendGrid not configured")
    message = Mail(
        from_email=SENDGRID_FROM_EMAIL,
        to_emails=to,
        subject=subject,
        plain_text_content=content,
    )
    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)
        return response.headers.get("X-Message-Id")
    except Exception as exc:  # pragma: no cover - API call
        print(f"Email failed: {exc}")
        return None
