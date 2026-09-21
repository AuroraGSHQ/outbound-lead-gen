"""The Analyst agent's content half: manual §12's quarterly benchmark-report
extract, drafted from real aggregated metrics and queued for owner review —
never auto-published, since a single self-serving conclusion in this asset
"destroys that perception permanently" per the manual.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.claude import BusinessProfile, ClaudeDrafter
from app.models import ActionCategory
from app.services import actions, metrics

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


def generate_quarterly_extract(session: Session, settings: Settings) -> dict[str, int]:
    snapshot = metrics.latest_snapshot(session)
    if snapshot is None:
        logger.info("No metric snapshot yet — skipping content extract.")
        return {"created": 0}

    summary = (
        f"Qualified conversations this month: {snapshot.qualified_conversations}\n"
        f"Cost per qualified conversation by channel: {snapshot.cost_per_qualified_conversation}\n"
        f"Close rate by channel: {snapshot.close_rate}\n"
        f"Cost per closed client: {snapshot.cost_per_closed_client}\n"
        f"Avg time to close (days): {snapshot.avg_time_to_close_days}\n"
        f"Referral share of new clients: {snapshot.referral_share}\n"
    )
    profile = _business_profile(settings)
    drafter = ClaudeDrafter(settings.anthropic_api_key, settings.anthropic_model)
    markdown = drafter.draft_content_extract(summary, profile)

    item = actions.create_action_item(
        session,
        title="Review this quarter's benchmark report extract",
        description=(
            "Drafted by the Analyst agent from real metrics. Review for accuracy "
            "and honesty (manual §12) before publishing anywhere external."
        ),
        category=ActionCategory.CONTENT.value,
        auto_executable=False,
        created_by="agent:analyst",
    )
    item.result = markdown
    session.commit()
    return {"created": 1}
