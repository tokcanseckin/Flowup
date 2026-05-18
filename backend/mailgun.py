"""Mailgun email sending helper.

Only MAILGUN_API_KEY is read from the environment (set in ~/.credentials on the server).
All other config is hardcoded as constants below.
"""

from __future__ import annotations

import base64
import json
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


def _send(
    to: str,
    subject: str,
    text: str | None = None,
    html: str | None = None,
    from_override: str | None = None,
    *,
    template: str | None = None,
    template_variables: dict | None = None,
) -> None:
    """POST to the Mailgun messages API. Silently skips if MAILGUN_API_KEY is not set."""
    api_key = os.environ.get("MAILGUN_API_KEY", "")
    if not api_key:
        return

    data: dict[str, str] = {
        "from": from_override or FROM_EMAIL,
        "to": to,
        "subject": subject,
    }
    if text:
        data["text"] = text
    if html:
        data["html"] = html
    if template:
        data["template"] = template
    if template_variables:
        data["t:variables"] = json.dumps(template_variables, ensure_ascii=False)

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


def send_welcome_email(*, to: str, display_name: str | None, t: dict) -> None:
    """Send a welcome email using the 'Welcome Email' Mailgun stored template."""
    name = display_name or ""
    text = (
        f"{t.get('greeting', 'Hi')} {name},\n\n"
        f"{t.get('body', '')}\n\n"
        f"{t.get('button', 'Browse Songs')}: {SITE_URL}\n\n"
        f"{t.get('footer', '')}"
    )
    variables = {
        "name": name,
        "greeting": t.get("greeting", "Hi"),
        "welcome_title": t.get("welcome_title", "Welcome to SingoLing!"),
        "body_text": t.get("body", ""),
        "footer_text": t.get("footer", ""),
    }
    _send(
        to=to,
        subject=t.get("subject", "Welcome to SingoLing \U0001f3b5"),
        text=text,
        from_override=FROM_EMAIL_INFO,
        template="Welcome Email",
        template_variables=variables,
    )


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


def send_password_reset(*, to: str, display_name: str | None, reset_url: str, t: dict | None = None) -> None:
    """Send a password-reset email using the 'password reset email' Mailgun stored template."""
    t = t or {}
    greeting = t.get("greeting", "Hi")
    body_text = t.get("body", "Click the button below to reset your password. This link expires in 1 hour.")
    button = t.get("button", "Reset Password")
    footer_text = t.get("footer", "If you didn't request this, you can safely ignore this email.")
    subject = t.get("subject", "Reset your SingoLing password")
    name = display_name or "there"
    text = (
        f"{greeting} {name},\n\n"
        f"{body_text}\n\n"
        f"{reset_url}\n\n"
        f"{footer_text}\n\n"
        "\u2014 The SingoLing team"
    )
    variables = {
        "welcome_title": subject,
        "name": name,
        "greeting": greeting,
        "body_text": body_text,
        "button_title": button,
        "action_url": reset_url,
        "footer_text": footer_text,
    }
    _send(
        to=to,
        subject=subject,
        text=text,
        from_override=FROM_EMAIL_INFO,
        template="password reset email",
        template_variables=variables,
    )
