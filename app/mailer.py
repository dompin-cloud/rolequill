"""Transactional email via the Resend HTTPS API (uses `requests`, no new deps).

Sending is optional: if RESEND_API_KEY isn't set, send_email() logs and returns False
so features degrade gracefully in local dev instead of crashing.
"""
import requests
from flask import current_app

_ENDPOINT = "https://api.resend.com/emails"


def is_configured():
    return bool(current_app.config.get("RESEND_API_KEY"))


def send_email(to, subject, html, text=None):
    """Send one email. Returns True on success, False otherwise. Never raises."""
    key = current_app.config.get("RESEND_API_KEY")
    if not key:
        current_app.logger.warning("Email skipped (RESEND_API_KEY unset): %r to %s",
                                   subject, to)
        return False
    payload = {
        "from": current_app.config.get("RESEND_FROM"),
        "to": [to],
        "subject": subject,
        "html": html,
    }
    if text:
        payload["text"] = text
    try:
        r = requests.post(_ENDPOINT, json=payload, timeout=10,
                          headers={"Authorization": f"Bearer {key}"})
        if r.status_code >= 400:
            current_app.logger.error("Resend send failed (%s): %s",
                                     r.status_code, r.text[:300])
            return False
        return True
    except requests.RequestException as e:  # network/timeout
        current_app.logger.error("Resend send error: %s", e)
        return False
