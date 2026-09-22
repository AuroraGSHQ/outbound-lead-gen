"""Apollo — production (video/photo). On-demand: when a campaign is flagged
as needing video/photo content, builds the shot list / deliverables /
deadline checklist a production crew needs — backs the "Content Production"
capability already advertised on the Aurora website but not yet backed by
an agent. Distinct from Apollo.io, the third-party lead-data API Voyager's
sourcing integrates with (see app/integrations/apollo.py) — same word,
unrelated systems.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.claude import BusinessProfile, ClaudeDrafter
from app.models import ActionCategory, ActionItem, AdCampaign
from app.services import actions


def _business_profile(settings: Settings) -> BusinessProfile:
    return BusinessProfile(
        business_name=settings.business_name,
        business_pitch=settings.business_pitch,
        sender_name=settings.sender_name,
        sender_title=settings.sender_title,
        calendly_link=settings.calendly_booking_link,
        physical_address=settings.business_physical_address,
    )


def create_production_brief(
    session: Session,
    settings: Settings,
    campaign_id: int,
    *,
    deadline: datetime | None = None,
) -> ActionItem:
    campaign = session.get(AdCampaign, campaign_id)
    if campaign is None:
        raise ValueError(f"No ad campaign {campaign_id}")

    profile = _business_profile(settings)
    drafter = ClaudeDrafter(settings.anthropic_api_key, settings.anthropic_model)
    brief = drafter.draft_production_brief(
        {"platform": campaign.platform, "creative": campaign.creative}, profile
    )

    item = actions.create_action_item(
        session,
        title=f"Production brief — {campaign.platform} {campaign.quarter_label}",
        description="Shot list and deliverables for the video/photo content this campaign needs.",
        category=ActionCategory.CONTENT.value,
        created_by="agent:apollo",
        payload={"ad_campaign_id": campaign.id},
        due_at=deadline,
    )
    item.result = brief
    session.commit()
    return item
