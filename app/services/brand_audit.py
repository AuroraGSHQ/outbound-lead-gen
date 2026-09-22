"""Starlight — brand identity. A lightweight on-demand check: reviews what's
on file for a client's brand voice/assets and raises what it finds as an
ActionItem for a person to act on — not a full brand-audit engagement.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.claude import BusinessProfile, ClaudeDrafter
from app.models import ActionCategory, ActionItem, Client
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


def check_brand_consistency(session: Session, settings: Settings, client_id: int) -> ActionItem:
    client = session.get(Client, client_id)
    if client is None:
        raise ValueError(f"No client {client_id}")

    brand_notes = client.notes.strip() or "no brand notes logged for this client yet"
    profile = _business_profile(settings)
    drafter = ClaudeDrafter(settings.anthropic_api_key, settings.anthropic_model)
    review = drafter.draft_brand_review({"company_name": client.company_name}, brand_notes, profile)

    item = actions.create_action_item(
        session,
        title=f"Brand consistency review — {client.company_name}",
        category=ActionCategory.ADMIN.value,
        client_id=client.id,
        created_by="agent:starlight",
    )
    item.result = review
    session.commit()
    return item
