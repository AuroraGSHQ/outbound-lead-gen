"""Chloris — creative fatigue watch. Manual §6: creative fatigue arrives
faster on narrow audiences, rotate every 3-4 weeks or performance quietly
degrades. Read-only over campaign age — it flags Promoter's (Pheme)
campaigns for a refresh, it never edits one itself.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import ActionCategory, ActionItem, AdCampaign, AdCampaignStatus
from app.services import actions

logger = logging.getLogger(__name__)

_REFRESH_AFTER_DAYS = 28


def check_creative_fatigue(session: Session, settings=None) -> dict[str, int]:
    stats = {"flags_raised": 0}
    cutoff = datetime.now(timezone.utc) - timedelta(days=_REFRESH_AFTER_DAYS)
    stale_campaigns = (
        session.query(AdCampaign)
        .filter(AdCampaign.status == AdCampaignStatus.PUBLISHED.value, AdCampaign.created_at <= cutoff)
        .all()
    )
    for campaign in stale_campaigns:
        title = f"Refresh creative — {campaign.platform} ({campaign.quarter_label})"
        if session.query(ActionItem).filter(ActionItem.title == title, ActionItem.status != "done").first():
            continue
        actions.create_action_item(
            session,
            title=title,
            description=(
                f"Live since {campaign.created_at.date().isoformat()} — past the 3-4 week window "
                "before fatigue sets in on a narrow audience."
            ),
            category=ActionCategory.ADS.value,
            created_by="agent:chloris",
            payload={"ad_campaign_id": campaign.id},
        )
        stats["flags_raised"] += 1

    logger.info("Creative fatigue check complete: %s", stats)
    return stats
