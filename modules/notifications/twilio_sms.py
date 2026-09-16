"""Thin Twilio REST wrapper: send one SMS from `twilio_phone_number` to
`owner_phone_number`. Kept dumb on purpose (matches the app/integrations/*.py
and modules/dialer/client.py pattern) — no DB access, no retry/give-up logic,
no knowledge of the `notifications` table. See service.py for all of that.
"""
from __future__ import annotations

from twilio.rest import Client

from app.config import Settings

# Twilio's hard cap for a single (possibly multi-segment) SMS body is ~1600
# chars; we stay comfortably under that for cost/readability reasons, not
# because we're close to hitting the technical limit.
MAX_MESSAGE_CHARS = 1500
TITLE_MAX_CHARS = 120
BODY_PREVIEW_CHARS = 300


def build_message_text(title: str, body: str) -> str:
    """Compose the single SMS string sent for a notification: the title,
    then a truncated preview of the body. Truncates the title and body
    independently first, then enforces the overall MAX_MESSAGE_CHARS cap as
    a final safety net.
    """
    title = (title or "").strip()
    body = (body or "").strip()

    if len(title) > TITLE_MAX_CHARS:
        title = title[: TITLE_MAX_CHARS - 1].rstrip() + "…"

    body_preview = body[:BODY_PREVIEW_CHARS]
    if len(body) > BODY_PREVIEW_CHARS:
        body_preview = body_preview.rstrip() + "…"

    text = f"{title}\n\n{body_preview}" if body_preview else title

    if len(text) > MAX_MESSAGE_CHARS:
        text = text[: MAX_MESSAGE_CHARS - 1].rstrip() + "…"

    return text


def send_sms(settings: Settings, title: str, body: str) -> str:
    """Send one SMS to the owner's phone. Returns the Twilio message SID on
    success. Raises whatever the twilio client raises (typically
    `twilio.base.exceptions.TwilioRestException`, but also a plain
    `ValueError` if required settings are missing) — callers (service.py)
    are responsible for catching that and recording it on the notification
    row instead of letting it propagate.
    """
    if not settings.owner_phone_number:
        raise ValueError("OWNER_PHONE_NUMBER is not set; cannot deliver SMS notification")
    if not settings.twilio_phone_number:
        raise ValueError("TWILIO_PHONE_NUMBER is not set; cannot deliver SMS notification")
    if not settings.twilio_account_sid or not settings.twilio_auth_token:
        raise ValueError(
            "TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN are not set; cannot deliver SMS notification"
        )

    client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
    message = client.messages.create(
        to=settings.owner_phone_number,
        from_=settings.twilio_phone_number,
        body=build_message_text(title, body),
    )
    return message.sid
