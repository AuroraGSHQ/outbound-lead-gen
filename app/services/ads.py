"""The Promoter agent: manual §4/§6/§14. Generates a full monthly campaign
brief per platform in the current quarter's budget allocation — audience,
budget split, ad copy by angle — as a ready-to-paste package. Live publishing
to Google/Meta stays a manual step (or a future integration — see
integrations/google_ads.py / meta_ads.py) until real developer API access
exists.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.claude import BusinessProfile, ClaudeDrafter
from app.models import ActionCategory, AdCampaign, AdCampaignStatus
from app.services import actions

logger = logging.getLogger(__name__)

_CALENDAR_PATH = "config/ads_budget_calendar.yaml"
_TARGETING_PATH = "config/ad_targeting.yaml"


def current_quarter_label(today: datetime | None = None) -> str:
    today = today or datetime.now(timezone.utc)
    quarter = (today.month - 1) // 3 + 1
    return f"{today.year}-Q{quarter}"


def _load_yaml(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text()) or {}


def _business_profile(settings: Settings) -> BusinessProfile:
    return BusinessProfile(
        business_name=settings.business_name,
        business_pitch=settings.business_pitch,
        sender_name=settings.sender_name,
        sender_title=settings.sender_title,
        calendly_link=settings.calendly_booking_link,
        physical_address=settings.business_physical_address,
    )


def generate_monthly_brief(
    session: Session, settings: Settings, *, quarter_label: str | None = None
) -> dict[str, int]:
    calendar = _load_yaml(_CALENDAR_PATH)
    targeting = _load_yaml(_TARGETING_PATH)
    quarter_label = quarter_label or current_quarter_label()
    quarter = calendar.get("quarters", {}).get(quarter_label)
    stats = {"campaigns_created": 0}
    if not quarter:
        logger.info("No budget calendar entry for %s — skipping ads brief.", quarter_label)
        return stats

    allocation = quarter.get("allocation", {})
    angles = targeting.get("angles", [{"name": "the leak"}])
    segment = targeting.get("segment_smb", {})
    profile = _business_profile(settings)
    drafter = ClaudeDrafter(settings.anthropic_api_key, settings.anthropic_model)

    for i, (platform, share) in enumerate(allocation.items()):
        if platform == "testing_only":
            continue  # nothing to brief yet — Q4-2026-style "build, don't buy" quarter
        existing = (
            session.query(AdCampaign)
            .filter(AdCampaign.platform == platform, AdCampaign.quarter_label == quarter_label)
            .first()
        )
        if existing is not None:
            continue

        angle = angles[i % len(angles)]
        copy = drafter.draft_ad_copy(platform, angle.get("name", "the leak"), str(segment), profile)

        brief_lines = [
            f"# {platform} — {quarter_label}",
            "",
            f"**Budget range this quarter:** {quarter.get('monthly_spend_range', 'n/a')} "
            f"(this platform's share: {share})",
            f"**Focus:** {quarter.get('focus', '')}",
            "",
            f"## Ad copy — angle: {angle.get('name')}",
            f"**Headline:** {copy['headline']}",
            "",
            copy["body"],
            "",
            "## Audience",
            f"{segment}",
        ]
        campaign = AdCampaign(
            platform=platform,
            quarter_label=quarter_label,
            status=AdCampaignStatus.DRAFT.value,
            targeting=segment,
            creative={"angle": angle.get("name"), **copy},
            brief_markdown="\n".join(brief_lines),
        )
        session.add(campaign)
        session.flush()

        actions.create_action_item(
            session,
            title=f"Review & publish the {quarter_label} {platform} campaign",
            description=(
                f"Brief is ready — see Ads > campaign #{campaign.id}. Publishing is a "
                "manual step in the platform's own ads manager until real API "
                "credentials are configured (see docs/SETUP.md)."
            ),
            category=ActionCategory.ADS.value,
            auto_executable=False,
            created_by="agent:promoter",
            payload={"ad_campaign_id": campaign.id},
        )
        stats["campaigns_created"] += 1

    session.commit()
    logger.info("Ads brief generation complete: %s", stats)
    return stats


def list_campaigns(session: Session) -> list[AdCampaign]:
    return session.query(AdCampaign).order_by(AdCampaign.created_at.desc()).all()


def mark_published(session: Session, campaign_id: int) -> AdCampaign:
    campaign = session.get(AdCampaign, campaign_id)
    if campaign is None:
        raise ValueError(f"No ad campaign {campaign_id}")
    campaign.status = AdCampaignStatus.PUBLISHED.value
    session.commit()
    return campaign
