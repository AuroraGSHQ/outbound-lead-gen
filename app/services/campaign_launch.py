"""Supernova — campaign launch. On-demand: the moment a campaign is marked
`ready_to_publish`, assembles a go-live checklist (assets, targeting, budget
confirmation) so nothing gets flipped live half-ready. Deterministic — it
reads what's already on the AdCampaign row, it doesn't draft anything new.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import ActionCategory, ActionItem, ActionStatus, AdCampaign, AdCampaignStatus
from app.services import actions


def _checklist_lines(campaign: AdCampaign) -> list[tuple[str, bool, str]]:
    return [
        ("Creative", bool(campaign.creative), "ad copy/creative attached" if campaign.creative else "missing — no creative on this campaign yet"),
        ("Targeting", bool(campaign.targeting), "audience segment set" if campaign.targeting else "missing — no targeting on this campaign yet"),
        (
            "Budget",
            bool(campaign.budget_monthly),
            f"${campaign.budget_monthly:,.0f}/mo" if campaign.budget_monthly else "missing — no monthly budget set",
        ),
        ("Brief", bool(campaign.brief_markdown), "brief on file" if campaign.brief_markdown else "missing — no brief drafted"),
    ]


def build_go_live_checklist(session: Session, settings, campaign_id: int) -> ActionItem:
    campaign = session.get(AdCampaign, campaign_id)
    if campaign is None:
        raise ValueError(f"No ad campaign {campaign_id}")

    title = f"Go-live checklist — {campaign.platform} {campaign.quarter_label}"
    existing = (
        session.query(ActionItem)
        .filter(ActionItem.title == title, ActionItem.status != ActionStatus.DONE.value)
        .first()
    )
    if existing is not None:
        return existing

    lines = _checklist_lines(campaign)
    all_ready = all(ok for _, ok, _ in lines)
    checklist_md = "\n".join(f"- [{'x' if ok else ' '}] {name}: {detail}" for name, ok, detail in lines)
    status_line = "Everything's in place — clear to publish." if all_ready else "Still missing something below."

    item = actions.create_action_item(
        session,
        title=title,
        description=f"{status_line}\n\n{checklist_md}",
        category=ActionCategory.ADS.value,
        created_by="agent:supernova",
        payload={"ad_campaign_id": campaign.id},
    )
    return item


def on_campaign_ready_to_publish(session: Session, settings, campaign_id: int) -> ActionItem | None:
    """Call this after moving an AdCampaign to `ready_to_publish` — a thin
    trigger wrapper so callers don't have to re-check the status themselves."""
    campaign = session.get(AdCampaign, campaign_id)
    if campaign is None or campaign.status != AdCampaignStatus.READY_TO_PUBLISH.value:
        return None
    return build_go_live_checklist(session, settings, campaign_id)
