"""Gravity — win-back. Manual §11's overlooked referral source: a
prospect you honestly told wasn't a fit remembers that, and is worth asking
for a referral six weeks later. This only ever produces a draft in the same
Approvals queue everything else uses — it never re-contacts anyone on its
own.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.claude import BusinessProfile, ClaudeDrafter
from app.models import Conversation, Lead, LeadStatus, Message, MessageDirection, MessageStatus
from app.services.approvals import compose_footer

logger = logging.getLogger(__name__)

_WINBACK_WINDOW_DAYS = 42  # six weeks, per the manual
_ELIGIBLE_STATUSES = {LeadStatus.CLOSED_LOST.value, LeadStatus.NOT_INTERESTED.value}


def _business_profile(settings: Settings) -> BusinessProfile:
    return BusinessProfile(
        business_name=settings.business_name,
        business_pitch=settings.business_pitch,
        sender_name=settings.sender_name,
        sender_title=settings.sender_title,
        calendly_link=settings.calendly_booking_link,
        physical_address=settings.business_physical_address,
    )


def check_winback_due(session: Session, settings: Settings) -> dict[str, int]:
    stats = {"drafted": 0}
    cutoff_recent = datetime.now(timezone.utc) - timedelta(days=_WINBACK_WINDOW_DAYS)
    cutoff_old = cutoff_recent - timedelta(days=14)  # a two-week window, not a single-day trigger

    candidates = (
        session.query(Lead)
        .filter(
            Lead.status.in_(_ELIGIBLE_STATUSES),
            Lead.unsubscribed.is_(False),
            Lead.updated_at <= cutoff_recent,
            Lead.updated_at >= cutoff_old,
        )
        .all()
    )

    profile = _business_profile(settings)
    drafter = ClaudeDrafter(settings.anthropic_api_key, settings.anthropic_model)

    _WINBACK_MARKER = "agent:gravity:winback"

    for lead in candidates:
        already = (
            session.query(Message)
            .join(Conversation)
            .filter(Conversation.lead_id == lead.id, Message.reviewer_note == _WINBACK_MARKER)
            .first()
        )
        if already is not None:
            continue

        draft = drafter.draft_client_email(
            "reconnecting to ask for a referral",
            {"contact_name": lead.contact_name, "company_name": lead.company_name},
            "We weren't a fit a while back, but I'd still value a referral if you know anyone "
            "dealing with the problem we talked about.",
            profile,
        )
        greeting = f"Hi {lead.contact_name.split(' ')[0]}," if lead.contact_name else "Hi,"
        body = (
            f"{greeting}\n\n{draft['body']}\n\nBest,\n{profile.sender_name}\n{profile.sender_title}"
            f"\n\n{compose_footer(profile)}"
        )
        conversation = Conversation(lead_id=lead.id, gmail_thread_id="", autopilot=False)
        session.add(conversation)
        session.flush()
        message = Message(
            conversation_id=conversation.id,
            direction=MessageDirection.OUTBOUND.value,
            subject=draft.get("subject") or "quick reconnect",
            body=body,
            status=MessageStatus.PENDING_APPROVAL.value,
            # Not shown to anyone — a marker so this sweep never re-drafts
            # the same lead's win-back ask twice, independent of whatever
            # wording the model happened to draft.
            reviewer_note=_WINBACK_MARKER,
        )
        session.add(message)
        session.commit()
        stats["drafted"] += 1

    logger.info("Win-back check complete: %s", stats)
    return stats
