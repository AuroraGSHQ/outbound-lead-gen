"""Nebula — content generation. Drafts long-form content (a blog post or
newsletter section) from a client's real story or the latest metrics,
queued for review like every other Claude-drafted asset. Distinct from
Observatory's strictly-metrics-grounded quarterly benchmark extract
(services/content.py) and from Pulsar's short-form social captions
(services/social_posts.py). On-demand only.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.claude import BusinessProfile, ClaudeDrafter
from app.models import ActionCategory, ActionItem, Client
from app.services import actions, metrics


def _business_profile(settings: Settings) -> BusinessProfile:
    return BusinessProfile(
        business_name=settings.business_name,
        business_pitch=settings.business_pitch,
        sender_name=settings.sender_name,
        sender_title=settings.sender_title,
        calendly_link=settings.calendly_booking_link,
        physical_address=settings.business_physical_address,
    )


def draft_longform_piece(
    session: Session,
    settings: Settings,
    *,
    client_id: int | None = None,
    topic: str | None = None,
) -> ActionItem:
    """If `client_id` is given, drafts a client-story piece from their notes.
    Otherwise drafts from the latest metrics snapshot, same as Observatory's
    quarterly extract but written as a newsletter section, not a benchmark
    report."""
    profile = _business_profile(settings)
    drafter = ClaudeDrafter(settings.anthropic_api_key, settings.anthropic_model)

    if client_id is not None:
        client = session.get(Client, client_id)
        if client is None:
            raise ValueError(f"No client {client_id}")
        subject = topic or f"{client.company_name}'s results"
        context = client.notes.strip() or "no notes logged on this client's record yet"
        title = f"Newsletter draft — {client.company_name}"
    else:
        snapshot = metrics.latest_snapshot(session)
        subject = topic or "this month's results"
        context = (
            f"Qualified conversations: {snapshot.qualified_conversations}\n"
            f"Close rate by channel: {snapshot.close_rate}\n"
            f"Referral share: {snapshot.referral_share}"
            if snapshot is not None
            else "no metric snapshot yet"
        )
        title = "Newsletter draft — this month's numbers"

    markdown = drafter.draft_longform_content(subject, context, profile)

    item = actions.create_action_item(
        session,
        title=title,
        description="Drafted by the Nebula agent — review before publishing anywhere external.",
        category=ActionCategory.CONTENT.value,
        client_id=client_id,
        created_by="agent:nebula",
    )
    item.result = markdown
    session.commit()
    return item
