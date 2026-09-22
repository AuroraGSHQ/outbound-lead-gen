"""Axis — proposals & contracts. Turns a client's intake-recommended tier
into a ready-to-send proposal (scope, price, the booked-job-floor
guarantee). Only ever acts on Horizon's (intake) structured output — it
never invents outreach of its own.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.claude import BusinessProfile, ClaudeDrafter
from app.models import ActionCategory, ActionItem, Client, IntakeCall
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


def build_proposal_markdown(session: Session, settings: Settings, client: Client) -> str:
    intake = (
        session.query(IntakeCall)
        .filter(IntakeCall.client_id == client.id)
        .order_by(IntakeCall.created_at.desc())
        .first()
    )
    context = "No intake call on file yet."
    if intake is not None:
        s = intake.structured or {}
        context = (
            f"Pain points: {s.get('pain_points')}. Goals: {s.get('goals')}. "
            f"Budget signal: {s.get('budget_signal')}."
        )
    profile = _business_profile(settings)
    drafter = ClaudeDrafter(settings.anthropic_api_key, settings.anthropic_model)
    return drafter.draft_proposal_document(
        {"company_name": client.company_name, "contact_name": client.contact_name},
        client.tier or "Core",
        context,
        profile,
    )


def generate_proposal(session: Session, settings: Settings, client_id: int) -> ActionItem:
    client = session.get(Client, client_id)
    if client is None:
        raise ValueError(f"No client {client_id}")
    markdown = build_proposal_markdown(session, settings, client)
    item = actions.create_action_item(
        session,
        title=f"Proposal draft — {client.company_name}",
        category=ActionCategory.ADMIN.value,
        client_id=client.id,
        created_by="agent:axis",
    )
    item.result = markdown
    session.commit()
    return item
