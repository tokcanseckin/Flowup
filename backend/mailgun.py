"""Mailgun email sending helper.

Only MAILGUN_API_KEY is read from the environment (set in ~/.credentials on the server).
All other config is hardcoded as constants below.
"""

from __future__ import annotations

import base64
import logging
import os
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

DOMAIN              = "singoling.com"
FROM_EMAIL          = f"SingoLing <noreply@{DOMAIN}>"
FROM_EMAIL_INFO     = "SingoLing <info@singoling.com>"
API_BASE            = "https://api.mailgun.net"
ADMIN_EMAIL         = "support@singoling.com"
SITE_URL            = "https://singoling.com"


def _send(to: str, subject: str, text: str, html: str | None = None, from_override: str | None = None) -> None:
    """POST to the Mailgun messages API. Silently skips if MAILGUN_API_KEY is not set."""
    api_key = os.environ.get("MAILGUN_API_KEY", "")
    if not api_key:
        return

    data: dict[str, str] = {
        "from": from_override or FROM_EMAIL,
        "to": to,
        "subject": subject,
        "text": text,
    }
    if html:
        data["html"] = html

    encoded = urllib.parse.urlencode(data).encode()
    credentials = base64.b64encode(f"api:{api_key}".encode()).decode()
    req = urllib.request.Request(
        f"{API_BASE}/v3/{DOMAIN}/messages",
        data=encoded,
        headers={"Authorization": f"Basic {credentials}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status not in (200, 202):
                raise RuntimeError(f"Mailgun returned status {resp.status}")
    except Exception as exc:
        log.error("mailgun: failed to send to %s (subject=%r): %s", to, subject, exc)
        raise


def send_support_notification(
    *,
    report_id: int,
    kind: str,
    user_name: str | None,
    user_email: str | None,
    song_title: str | None,
    message: str | None,
) -> None:
    """Email the admin when a new support ticket is submitted."""
    lines = [
        f"New support ticket #{report_id}",
        f"Kind:  {kind}",
        f"User:  {user_name or 'unknown'} <{user_email or 'no email'}>",
    ]
    if song_title:
        lines.append(f"Song:  {song_title}")
    if message:
        lines.append(f"\nMessage:\n{message}")

    _send(
        to=ADMIN_EMAIL,
        subject=f"[SingoLing] Support ticket #{report_id} — {kind}",
        text="\n".join(lines),
    )


def send_password_reset(*, to: str, display_name: str | None, reset_url: str) -> None:
    """Send a password-reset email to a user."""
    name = display_name or "there"
    text = (
        f"Hi {name},\n\n"
        "Click the link below to reset your SingoLing password. "
        "This link expires in 1 hour.\n\n"
        f"{reset_url}\n\n"
        "If you did not request a password reset, you can safely ignore this email.\n\n"
        "— The SingoLing team"
    )
    html = (
        f"<p>Hi {name},</p>"
        "<p>Click the button below to reset your SingoLing password. "
        "This link expires in 1 hour.</p>"
        f'<p><a href="{reset_url}" '
        'style="display:inline-block;background:#6366f1;color:#fff;'
        'padding:10px 24px;border-radius:8px;text-decoration:none;font-weight:600;">'
        "Reset password</a></p>"
        "<p style=\"color:#6b7280;font-size:13px;\">If you did not request a password reset, "
        "you can safely ignore this email.</p>"
        "<p>— The SingoLing team</p>"
    )
    _send(to=to, subject="Reset your SingoLing password", text=text, html=html, from_override=FROM_EMAIL_INFO)
