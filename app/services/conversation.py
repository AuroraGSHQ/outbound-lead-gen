"""Poll Gmail for replies on active threads, classify intent, and draft (or
send) the next message.

Unsubscribe requests are the one thing this module sends immediately
regardless of APPROVAL_MODE — honoring an opt-out promptly is a compliance
requirement, not a sales decision, so it doesn't belong in the human queue.
Everything else respects draft-and-approve unless the owner has flipped a
specific conversation to autopilot.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations import gmail
from app.integrations.claude import BusinessProfile, ClaudeDrafter
from app.models import Conversation, LeadStatus, Message, MessageDirection, MessageStatus
from app.services.approvals import compose_footer, send_message

logger = logging.getLogger(__name__)

_INTENT_TO_LEAD_STATUS = {
    "interested": LeadStatus.INTERESTED.value,
    "scheduling": LeadStatus.INTERESTED.value,
    "wants_more_info": LeadStatus.REPLIED.value,
    "objection": LeadStatus.REPLIED.value,
    "other": LeadStatus.REPLIED.value,
    "not_interested": LeadStatus.NOT_INTERESTED.value,
}


def _business_profile(settings: Settings) -> BusinessProfile:
    return BusinessProfile(
        business_name=settings.business_name,
        business_pitch=settings.business_pitch,
        sender_name=settings.sender_name,
        sender_title=settings.sender_title,
        calendly_link=settings.calendly_booking_link,
        physical_address=settings.business_physical_address,
    )


def _thread_text(conversation: Conversation) -> str:
    lines = []
    for m in conversation.messages:
        speaker = "Us" if m.direction == MessageDirection.OUTBOUND.value else "Prospect"
        lines.append(f"{speaker}: {m.body}")
    return "\n\n".join(lines)


def poll_and_process_replies(session: Session, settings: Settings) -> dict[str, int]:
    stats = {"threads_checked": 0, "new_inbound": 0, "auto_sent": 0, "queued_for_approval": 0}
    service = gmail.build_service(settings.gmail_token_path)
    drafter = ClaudeDrafter(settings.anthropic_api_key, settings.anthropic_model)
    profile = _business_profile(settings)

    conversations = (
        session.query(Conversation)
        .filter(Conversation.gmail_thread_id != "", Conversation.status == "active")
        .all()
    )

    for conversation in conversations:
        stats["threads_checked"] += 1
        known_ids = {m.gmail_message_id for m in conversation.messages if m.gmail_message_id}
        try:
            thread_message_ids = gmail.list_message_ids_in_thread(
                service, conversation.gmail_thread_id
            )
        except Exception:  # noqa: BLE001 - a single bad thread shouldn't kill the sweep
            logger.exception("Failed to list thread %s", conversation.gmail_thread_id)
            continue

        new_ids = [mid for mid in thread_message_ids if mid not in known_ids]
        if not new_ids:
            continue

        lead = conversation.lead
        for mid in new_ids:
            full = gmail.get_message(service, mid)
            is_ours = settings.gmail_sender_email.lower() in full["from"].lower()
            if is_ours:
                continue  # our own sent copy; already recorded when we sent it

            inbound = Message(
                conversation_id=conversation.id,
                direction=MessageDirection.INBOUND.value,
                subject=full["subject"],
                body=full["body"],
                status=MessageStatus.RECEIVED.value,
                gmail_message_id=full["id"],
            )
            session.add(inbound)
            session.commit()
            stats["new_inbound"] += 1

            classification = drafter.classify_intent(_thread_text(conversation))
            intent = classification["intent"]
            inbound.detected_intent = intent
            session.commit()

            if intent == "unsubscribe_request":
                lead.unsubscribed = True
                lead.status = LeadStatus.UNSUBSCRIBED.value
                session.commit()
                reply = drafter.draft_reply(
                    {"contact_name": lead.contact_name, "company_name": lead.company_name},
                    _thread_text(conversation),
                    intent,
                    profile,
                )
                confirmation = Message(
                    conversation_id=conversation.id,
                    direction=MessageDirection.OUTBOUND.value,
                    subject=f"Re: {inbound.subject}",
                    body=reply["body"],
                    status=MessageStatus.APPROVED.value,
                )
                session.add(confirmation)
                session.commit()
                send_message(session, settings, confirmation.id)
                stats["auto_sent"] += 1
                continue

            lead.status = _INTENT_TO_LEAD_STATUS.get(intent, LeadStatus.REPLIED.value)
            session.commit()

            if intent == "out_of_office":
                continue  # nothing useful to reply to yet

            reply = drafter.draft_reply(
                {"contact_name": lead.contact_name, "company_name": lead.company_name},
                _thread_text(conversation),
                intent,
                profile,
            )
            body = f"{reply['body']}\n\n{compose_footer(profile)}"
            reply_message = Message(
                conversation_id=conversation.id,
                direction=MessageDirection.OUTBOUND.value,
                subject=f"Re: {inbound.subject}",
                body=body,
                status=MessageStatus.PENDING_APPROVAL.value,
            )
            session.add(reply_message)
            session.commit()

            if conversation.autopilot or settings.is_autonomous:
                send_message(session, settings, reply_message.id)
                stats["auto_sent"] += 1
            else:
                stats["queued_for_approval"] += 1

    logger.info("Reply poll complete: %s", stats)
    return stats
