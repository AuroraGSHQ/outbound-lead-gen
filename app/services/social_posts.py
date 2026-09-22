"""Pulsar — social media. On-demand: drafts an organic social caption for a
client's own result or a recent case study, in the same style as the rest
of the app — Claude writes the draft, it lands as an ActionItem for review,
nothing posts on its own. Distinct from Orbit's paid ad copy
(services/ads.py) and Nebula's long-form content (services/longform_content.py).
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.claude import BusinessProfile, ClaudeDrafter
from app.models import ActionCategory, ActionItem, Client
from app.services import actions

_DEFAULT_PLATFORM = "linkedin"


def _business_profile(settings: Settings) -> BusinessProfile:
    return BusinessProfile(
        business_name=settings.business_name,
        business_pitch=settings.business_pitch,
        sender_name=settings.sender_name,
        sender_title=settings.sender_title,
        calendly_link=settings.calendly_booking_link,
        physical_address=settings.business_physical_address,
    )


def _latest_case_study(session: Session, client_id: int) -> ActionItem | None:
    return (
        session.query(ActionItem)
        .filter(
            ActionItem.client_id == client_id,
            ActionItem.category == ActionCategory.CONTENT.value,
            ActionItem.title.like("Case study draft%"),
        )
        .order_by(ActionItem.created_at.desc())
        .first()
    )


def draft_social_post(
    session: Session,
    settings: Settings,
    *,
    client_id: int,
    platform: str = _DEFAULT_PLATFORM,
    context: str | None = None,
) -> ActionItem:
    client = session.get(Client, client_id)
    if client is None:
        raise ValueError(f"No client {client_id}")

    if context is None:
        case_study = _latest_case_study(session, client_id)
        context = (case_study.result if case_study and case_study.result else "") or (
            client.notes.strip() or "no notes on file for this client yet"
        )

    profile = _business_profile(settings)
    drafter = ClaudeDrafter(settings.anthropic_api_key, settings.anthropic_model)
    caption = drafter.draft_social_caption(
        {"company_name": client.company_name}, context, platform, profile
    )

    item = actions.create_action_item(
        session,
        title=f"Social post draft — {client.company_name} ({platform})",
        description="Drafted for review — nothing posts automatically.",
        category=ActionCategory.CONTENT.value,
        client_id=client.id,
        created_by="agent:pulsar",
    )
    item.result = caption["caption"]
    session.commit()
    return item
