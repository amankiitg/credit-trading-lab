"""Best-effort email alerting for execution guardrails (sprint v8.6).

Not part of the trading logic -- a failure here must never interrupt the
execution cron. send_alert_email() swallows every exception and returns
False on any problem (missing config, API error, network failure), only
ever logging a warning.

Sends via the Resend HTTP API (https://resend.com), not SMTP: Render
blocks outbound traffic to SMTP ports (25/465/587) on free-tier services,
but plain HTTPS (the Resend API endpoint, port 443) is never blocked.
Uses Resend's shared onboarding@resend.dev sender by default, since this
only ever sends to one operator-configured recipient -- no domain
verification needed.
"""

from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"
DEFAULT_FROM = "credit-trading-lab <onboarding@resend.dev>"


def send_alert_email(subject: str, body: str) -> bool:
    """Send a plaintext alert email via Resend. Returns True on success.

    Reads RESEND_API_KEY and ALERT_EMAIL_TO (falls back to ALLOWED_EMAIL,
    the operator's own address) from the environment. RESEND_FROM
    optionally overrides the shared onboarding@resend.dev sender once a
    verified domain is set up. Send is skipped -- not an error -- when
    RESEND_API_KEY or a recipient aren't configured, so local dev and
    tests never need real credentials.
    """
    api_key = os.environ.get("RESEND_API_KEY")
    to_addr = os.environ.get("ALERT_EMAIL_TO") or os.environ.get("ALLOWED_EMAIL")
    from_addr = os.environ.get("RESEND_FROM") or DEFAULT_FROM

    if not api_key or not to_addr:
        logger.info(
            "alert email skipped -- RESEND_API_KEY/ALERT_EMAIL_TO not fully configured"
        )
        return False

    try:
        resp = requests.post(
            RESEND_API_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "from": from_addr,
                "to": [to_addr],
                "subject": subject,
                "text": body,
            },
            timeout=15,
        )
        resp.raise_for_status()
        logger.info("alert email sent to %s: %s", to_addr, subject)
        return True
    except Exception as exc:
        logger.warning("alert email failed: %s", exc)
        return False
