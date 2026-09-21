"""Peitho's phone channel — SMS and one-way outbound voice, built on the
same draft-and-approve queue email uses. A lead needs a phone number on
file (Lead.contact_phone) before either channel can be used; there's no
guessing or reusing another lead's number.

Voice here is a recorded script played on an outbound call (ElevenLabs
synthesizes it, Twilio places the call and plays it back) — not a live
two-way conversation. That's a distinct, much bigger feature (real-time
speech-to-text + streaming Claude + streaming TTS over Twilio Media
Streams) and isn't built here.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.claude import BusinessProfile, ClaudeDrafter
from app.models import (
    Conversation,
    Lead,
    Message,
    MessageChannel,
    MessageDirection,
    MessageStatus,
)
logger = logging.getLogger(__name__)

_SMS_OPT_OUT = " Reply STOP to opt out."


def _business_profile(settings: Settings) -> BusinessProfile:
    return BusinessProfile(
        business_name=settings.business_name,
        business_pitch=settings.business_pitch,
        sender_name=settings.sender_name,
        sender_title=settings.sender_title,
        calendly_link=settings.calendly_booking_link,
        physical_address=settings.business_physical_address,
    )


def _lead_dict(lead: Lead) -> dict:
    return {
        "contact_name": lead.contact_name,
        "contact_title": lead.contact_title,
        "company_name": lead.company_name,
        "industry": lead.industry,
        "location": lead.location,
    }


def _get_or_create_conversation(session: Session, lead: Lead) -> Conversation:
    conversation = Conversation(lead_id=lead.id, gmail_thread_id="", autopilot=False)
    session.add(conversation)
    session.flush()
    return conversation


def draft_sms_for_lead(session: Session, settings: Settings, lead_id: int) -> Message:
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise ValueError(f"No lead {lead_id}")
    if not lead.contact_phone:
        raise ValueError(f"{lead.company_name} has no phone number on file — add one before drafting an SMS.")

    drafter = ClaudeDrafter(settings.anthropic_api_key, settings.anthropic_model)
    body = drafter.draft_sms_first_touch(_lead_dict(lead), _business_profile(settings))
    body = f"{body}{_SMS_OPT_OUT}" if body else body

    conversation = _get_or_create_conversation(session, lead)
    message = Message(
        conversation_id=conversation.id,
        direction=MessageDirection.OUTBOUND.value,
        channel=MessageChannel.SMS.value,
        body=body,
        status=MessageStatus.PENDING_APPROVAL.value,
    )
    session.add(message)
    session.commit()
    logger.info("Drafted SMS for lead %s", lead.id)
    return message


def draft_call_for_lead(session: Session, settings: Settings, lead_id: int) -> Message:
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise ValueError(f"No lead {lead_id}")
    if not lead.contact_phone:
        raise ValueError(f"{lead.company_name} has no phone number on file — add one before drafting a call script.")

    drafter = ClaudeDrafter(settings.anthropic_api_key, settings.anthropic_model)
    script = drafter.draft_call_script(_lead_dict(lead), _business_profile(settings))

    conversation = _get_or_create_conversation(session, lead)
    message = Message(
        conversation_id=conversation.id,
        direction=MessageDirection.OUTBOUND.value,
        channel=MessageChannel.VOICE.value,
        body=script,
        status=MessageStatus.PENDING_APPROVAL.value,
    )
    session.add(message)
    session.commit()
    logger.info("Drafted call script for lead %s", lead.id)
    return message


def receive_sms_reply(session: Session, from_number: str, body: str) -> Message | None:
    """Logs an inbound SMS against whichever lead owns that phone number.
    Returns None (and just logs a warning) if no lead matches — an unknown
    number texting in isn't something we can safely attribute."""
    lead = session.query(Lead).filter(Lead.contact_phone == from_number).one_or_none()
    if lead is None:
        logger.warning("Inbound SMS from unrecognized number %s — dropped.", from_number)
        return None

    conversation = (
        session.query(Conversation)
        .filter(Conversation.lead_id == lead.id)
        .order_by(Conversation.created_at.desc())
        .first()
    )
    if conversation is None:
        conversation = _get_or_create_conversation(session, lead)

    message = Message(
        conversation_id=conversation.id,
        direction=MessageDirection.INBOUND.value,
        channel=MessageChannel.SMS.value,
        body=body,
        status=MessageStatus.RECEIVED.value,
    )
    session.add(message)
    session.commit()
    logger.info("Logged inbound SMS from lead %s", lead.id)
    return message
