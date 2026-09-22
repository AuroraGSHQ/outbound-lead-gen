"""Zenith — case study builder. Manual §1: "a result from a business exactly
like theirs, with the numbers shown" is the single most persuasive thing
Aurora has. Once a client's been active long enough to have a real before/
after story, this drafts it for review — never publishes on its own.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.claude import BusinessProfile, ClaudeDrafter
from app.models import ActionCategory, ActionItem, Client, ClientStatus
from app.services import actions

logger = logging.getLogger(__name__)

_ELIGIBLE_AFTER_DAYS = 90


def _business_profile(settings: Settings) -> BusinessProfile:
    return BusinessProfile(
        business_name=settings.business_name,
        business_pitch=settings.business_pitch,
        sender_name=settings.sender_name,
        sender_title=settings.sender_title,
        calendly_link=settings.calendly_booking_link,
        physical_address=settings.business_physical_address,
    )


def check_case_study_candidates(session: Session, settings: Settings) -> dict[str, int]:
    stats = {"drafted": 0}
    cutoff = datetime.now(timezone.utc) - timedelta(days=_ELIGIBLE_AFTER_DAYS)
    clients = (
        session.query(Client)
        .filter(Client.status == ClientStatus.ACTIVE.value, Client.start_date.is_not(None), Client.start_date <= cutoff)
        .all()
    )
    for client in clients:
        title = f"Case study draft — {client.company_name}"
        if session.query(ActionItem).filter(ActionItem.title == title).first() is not None:
            continue

        profile = _business_profile(settings)
        drafter = ClaudeDrafter(settings.anthropic_api_key, settings.anthropic_model)
        metrics_summary = client.notes.strip() or "no metrics logged on the client record yet"
        markdown = drafter.draft_case_study(
            {"company_name": client.company_name, "tier": client.tier}, metrics_summary, profile
        )

        item = actions.create_action_item(
            session,
            title=title,
            description="Drafted from the client's logged notes/metrics — verify the numbers before publishing.",
            category=ActionCategory.CONTENT.value,
            client_id=client.id,
            created_by="agent:zenith",
        )
        item.result = markdown
        session.commit()
        stats["drafted"] += 1

    logger.info("Case study check complete: %s", stats)
    return stats
