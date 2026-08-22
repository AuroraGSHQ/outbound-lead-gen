"""Turn QUEUED leads into first-touch email drafts (and stale threads into follow-ups).

Drafts always land in the `messages` table with status PENDING_APPROVAL. If
APPROVAL_MODE=autonomous, this module immediately hands them to
services.approvals.send_message right after creating them — but the queue
step always happens first, so draft-and-approve and autonomous share one
code path with a single branch point.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.claude import BusinessProfile, ClaudeDrafter
from app.models import Conversation, Lead, LeadStatus, Message, MessageDirection, MessageStatus
from app.services.approvals import compose_footer, send_message

logger = logging.getLogger(__name__)


def _business_profile(settings: Settings) -> BusinessProfile:
    return BusinessProfile(
        business_name=settings.business_name,
        business_pitch=settings.business_pitch,
        sender_name=settings.sender_name,
        sender_title=settings.sender_title,
        calendly_link=settings.calendly_booking_link,
        physical_address=settings.business_physical_address,
    )


def _sent_today_count(session: Session) -> int:
    since = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        session.query(Message)
        .filter(
            Message.direction == MessageDirection.OUTBOUND.value,
            Message.status == MessageStatus.SENT.value,
            Message.sent_at >= since,
        )
        .count()
    )


def generate_pending_drafts(session: Session, settings: Settings) -> dict[str, int]:
    stats = {"drafted": 0, "sent": 0, "skipped_cap": 0}
    remaining = max(0, settings.daily_outreach_cap - _sent_today_count(session))
    if remaining == 0:
        return stats

    profile = _business_profile(settings)
    drafter = ClaudeDrafter(settings.anthropic_api_key, settings.anthropic_model)

    leads = (
        session.query(Lead)
        .filter(Lead.status == LeadStatus.QUEUED.value, Lead.unsubscribed.is_(False))
        .order_by(Lead.icp_score.desc())
        .limit(remaining)
        .all()
    )

    for lead in leads:
        draft = drafter.draft_first_touch_email(
            {
                "contact_name": lead.contact_name,
                "contact_title": lead.contact_title,
                "company_name": lead.company_name,
                "industry": lead.industry,
                "company_size": lead.company_size,
                "location": lead.location,
                "description": lead.notes,
            },
            profile,
        )
        greeting = f"Hi {lead.contact_name.split(' ')[0]}," if lead.contact_name else "Hi,"
        body = (
            f"{greeting}\n\n{draft['body']}\n\nBest,\n{profile.sender_name}\n{profile.sender_title}"
            f"\n\n{compose_footer(profile)}"
        )

        conversation = Conversation(lead_id=lead.id, gmail_thread_id="", autopilot=False)
        session.add(conversation)
        session.flush()  # get conversation.id

        message = Message(
            conversation_id=conversation.id,
            direction=MessageDirection.OUTBOUND.value,
            subject=draft.get("subject") or f"Quick question for {lead.company_name}",
            body=body,
            status=MessageStatus.PENDING_APPROVAL.value,
        )
        session.add(message)
        session.commit()
        stats["drafted"] += 1

        if settings.is_autonomous:
            send_message(session, settings, message.id)
            stats["sent"] += 1

    logger.info("Outreach draft generation complete: %s", stats)
    return stats


def generate_followups_for_stale_conversations(
    session: Session, settings: Settings, *, stale_after_days: int = 4
) -> dict[str, int]:
    """Draft a follow-up for threads where our last message got no reply."""
    stats = {"followups_drafted": 0}
    profile = _business_profile(settings)
    drafter = ClaudeDrafter(settings.anthropic_api_key, settings.anthropic_model)
    cutoff = datetime.now(timezone.utc) - timedelta(days=stale_after_days)

    stale_conversations = (
        session.query(Conversation)
        .join(Lead)
        .filter(Lead.status == LeadStatus.CONTACTED.value, Lead.unsubscribed.is_(False))
        .all()
    )

    for conversation in stale_conversations:
        if not conversation.messages:
            continue
        last = conversation.messages[-1]
        already_followed_up = sum(
            1 for m in conversation.messages if m.direction == MessageDirection.OUTBOUND.value
        )
        if (
            last.direction != MessageDirection.OUTBOUND.value
            or last.status != MessageStatus.SENT.value
            or not last.sent_at
            or last.sent_at > cutoff
            or already_followed_up >= 3  # cap: at most 2 follow-ups after the first touch
        ):
            continue

        lead = conversation.lead
        draft = drafter.draft_followup(
            {"contact_name": lead.contact_name, "company_name": lead.company_name}, profile
        )
        greeting = f"Hi {lead.contact_name.split(' ')[0]}," if lead.contact_name else "Hi,"
        body = f"{greeting}\n\n{draft['body']}\n\nBest,\n{profile.sender_name}\n\n{compose_footer(profile)}"

        message = Message(
            conversation_id=conversation.id,
            direction=MessageDirection.OUTBOUND.value,
            subject=draft.get("subject") or f"Re: {last.subject}",
            body=body,
            status=MessageStatus.PENDING_APPROVAL.value,
        )
        session.add(message)
        session.commit()
        stats["followups_drafted"] += 1

        if settings.is_autonomous:
            send_message(session, settings, message.id)

    logger.info("Follow-up sweep complete: %s", stats)
    return stats
