"""The draft-and-approve queue: approve/reject/edit, and the one place that
actually calls out to Gmail to send an email.

Every outbound send in the whole app — first touch, reply, follow-up, in
draft-and-approve mode or autonomous mode — goes through send_message() so
there's exactly one code path that hits the network and updates state.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations import elevenlabs, gmail, twilio_sms
from app.integrations.claude import BusinessProfile
from app.models import LeadStatus, Message, MessageChannel, MessageDirection, MessageStatus

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {
    LeadStatus.MEETING_BOOKED.value,
    LeadStatus.CLOSED_LOST.value,
    LeadStatus.UNSUBSCRIBED.value,
    LeadStatus.NOT_INTERESTED.value,
}


def compose_footer(profile: BusinessProfile) -> str:
    address = profile.physical_address or "[add your business address in .env]"
    return (
        f"{profile.business_name} | {address}\n"
        "If you'd rather not hear from us again, just reply \"unsubscribe\" and we'll stop."
    )


def send_message(session: Session, settings: Settings, message_id: int) -> Message:
    """The one send path for every outbound channel. Which network call it
    makes depends on message.channel — email via Gmail, sms/voice via
    Twilio (voice is synthesized by ElevenLabs first) — but every channel
    lands here, keeps the same status transitions, and updates the same
    lead/conversation state."""
    message = session.get(Message, message_id)
    if message is None:
        raise ValueError(f"No message with id {message_id}")
    conversation = message.conversation
    lead = conversation.lead

    if lead.unsubscribed:
        message.status = MessageStatus.REJECTED.value
        message.reviewer_note = "Blocked: lead has unsubscribed."
        session.commit()
        logger.warning("Refused to send to unsubscribed lead %s", lead.contact_email)
        return message

    channel = message.channel or MessageChannel.EMAIL.value
    if channel == MessageChannel.SMS.value:
        _send_sms(settings, message, lead)
    elif channel == MessageChannel.VOICE.value:
        _send_voice(settings, message, lead)
    else:
        _send_email(settings, conversation, message, lead)

    message.status = MessageStatus.SENT.value
    message.sent_at = datetime.now(timezone.utc)

    if lead.status not in _TERMINAL_STATUSES:
        lead.status = LeadStatus.CONTACTED.value

    session.commit()
    logger.info("Sent %s message %s to lead %s", channel, message.id, lead.id)
    return message


def _send_email(settings: Settings, conversation, message: Message, lead) -> None:
    service = gmail.build_service(settings.gmail_token_path)

    in_reply_to = None
    for m in reversed(conversation.messages):
        if m.direction == MessageDirection.INBOUND.value and m.gmail_message_id:
            in_reply_to = m.gmail_message_id
            break

    sent = gmail.send_email(
        service,
        sender=settings.gmail_sender_email,
        to=lead.contact_email,
        subject=message.subject or "",
        body=message.body,
        thread_id=conversation.gmail_thread_id or None,
        in_reply_to=in_reply_to,
    )
    message.gmail_message_id = sent.message_id
    if not conversation.gmail_thread_id:
        conversation.gmail_thread_id = sent.thread_id


def _send_sms(settings: Settings, message: Message, lead) -> None:
    result = twilio_sms.send_sms(
        settings.twilio_account_sid, settings.twilio_auth_token, settings.twilio_from_number, lead.contact_phone, message.body
    )
    message.twilio_sid = result.get("sid", "")


def _send_voice(settings: Settings, message: Message, lead) -> None:
    clip_path = Path(settings.voice_clip_dir) / f"{message.id}.mp3"
    elevenlabs.synthesize_to_file(settings.elevenlabs_api_key, settings.elevenlabs_voice_id, message.body, clip_path)
    if not settings.public_base_url:
        raise ValueError("PUBLIC_BASE_URL is not set — Twilio needs a public URL to fetch the voice clip.")
    audio_url = f"{settings.public_base_url.rstrip('/')}/voice-clips/{message.id}.mp3"
    result = twilio_sms.place_call_with_audio(
        settings.twilio_account_sid, settings.twilio_auth_token, settings.twilio_from_number, lead.contact_phone, audio_url
    )
    message.voice_clip_path = str(clip_path)
    message.twilio_sid = result.get("sid", "")


def approve_and_send(
    session: Session, settings: Settings, message_id: int, edited_body: str | None = None
) -> Message:
    message = session.get(Message, message_id)
    if message is None:
        raise ValueError(f"No message with id {message_id}")
    if edited_body is not None and edited_body.strip():
        message.body = edited_body
    message.status = MessageStatus.APPROVED.value
    session.commit()
    return send_message(session, settings, message_id)


def reject(session: Session, message_id: int, reason: str = "") -> Message:
    message = session.get(Message, message_id)
    if message is None:
        raise ValueError(f"No message with id {message_id}")
    message.status = MessageStatus.REJECTED.value
    message.reviewer_note = reason
    session.commit()
    return message


def pending_approvals(session: Session) -> list[Message]:
    return (
        session.query(Message)
        .filter(Message.status == MessageStatus.PENDING_APPROVAL.value)
        .order_by(Message.created_at.asc())
        .all()
    )
