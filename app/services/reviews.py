"""Radiance — reputation & reviews. No Google/Facebook review API is wired in
(there's nothing to poll), so this covers what's honestly buildable without
one: a scheduled nudge to ask happy clients for a review, and an on-demand
"log what a review said, get a reply drafted" flow for whoever's watching
the actual review pages by hand.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.claude import BusinessProfile, ClaudeDrafter
from app.models import ActionCategory, ActionItem, Client, ClientStatus

logger = logging.getLogger(__name__)

_REVIEW_ASK_AFTER_DAYS = 60


def _business_profile(settings: Settings) -> BusinessProfile:
    return BusinessProfile(
        business_name=settings.business_name,
        business_pitch=settings.business_pitch,
        sender_name=settings.sender_name,
        sender_title=settings.sender_title,
        calendly_link=settings.calendly_booking_link,
        physical_address=settings.business_physical_address,
    )


def check_review_requests_due(session: Session, settings: Settings) -> dict[str, int]:
    stats = {"requests_created": 0}
    cutoff = datetime.now(timezone.utc) - timedelta(days=_REVIEW_ASK_AFTER_DAYS)
    clients = (
        session.query(Client)
        .filter(Client.status == ClientStatus.ACTIVE.value, Client.start_date.is_not(None), Client.start_date <= cutoff)
        .all()
    )
    for client in clients:
        title = f"Ask {client.company_name} for a review"
        exists = session.query(ActionItem).filter(ActionItem.title == title).first()
        if exists is not None:
            continue
        from app.services import actions

        item = actions.create_action_item(
            session,
            title=title,
            description="60 days in and things are going well — a good moment to ask for a public review.",
            category=ActionCategory.REVIEWS.value,
            client_id=client.id,
            created_by="agent:radiance",
        )
        item.result = (
            "Ask: \"Glad it's working out — would you mind leaving a quick Google review? "
            "Takes about a minute and it genuinely helps.\""
        )
        session.commit()
        stats["requests_created"] += 1

    logger.info("Review request check complete: %s", stats)
    return stats


def log_review_and_draft_response(
    session: Session, settings: Settings, *, client_id: int | None, review_text: str, rating: str
) -> ActionItem:
    profile = _business_profile(settings)
    drafter = ClaudeDrafter(settings.anthropic_api_key, settings.anthropic_model)
    reply = drafter.draft_review_response(review_text, rating, profile)

    from app.services import actions

    item = actions.create_action_item(
        session,
        title=f"Reply to a {rating}-star review",
        description=review_text,
        category=ActionCategory.REVIEWS.value,
        client_id=client_id,
        created_by="agent:radiance",
    )
    item.result = f"Drafted reply (paste into Google/Facebook by hand):\n\n{reply}"
    session.commit()
    return item
