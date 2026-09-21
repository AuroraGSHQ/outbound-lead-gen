"""The Concierge agent: turns raw discovery/intro-call notes into a
structured client record and a concrete, partly-automated action plan. This
is the direct answer to "after the client introduction call, enter in the
details and either have the actions automatically performed or the steps
needed" — every generated next step becomes an ActionItem (services/actions.py),
auto-run where there's a safe handler, otherwise a checklist item.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.claude import BusinessProfile, ClaudeDrafter
from app.models import Client, ClientStatus, IntakeCall, Lead, User
from app.services import actions

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


def _get_or_create_client(session: Session, lead: Lead) -> Client:
    client = session.query(Client).filter(Client.lead_id == lead.id).first()
    if client is not None:
        return client
    client = Client(
        lead_id=lead.id,
        company_name=lead.company_name,
        contact_name=lead.contact_name,
        contact_email=lead.contact_email,
        status=ClientStatus.PROSPECT.value,
    )
    session.add(client)
    session.flush()
    return client


def create_intake(
    session: Session, settings: Settings, lead_id: int, raw_notes: str, user: User
) -> IntakeCall:
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise ValueError(f"No lead {lead_id}")

    profile = _business_profile(settings)
    drafter = ClaudeDrafter(settings.anthropic_api_key, settings.anthropic_model)
    structured = drafter.analyze_intro_call(
        raw_notes,
        {
            "contact_name": lead.contact_name,
            "contact_title": lead.contact_title,
            "company_name": lead.company_name,
            "industry": lead.industry,
            "location": lead.location,
        },
        profile,
    )

    client = _get_or_create_client(session, lead)
    if structured.get("recommended_tier"):
        client.tier = structured["recommended_tier"]
    session.commit()

    intake = IntakeCall(
        lead_id=lead.id,
        client_id=client.id,
        raw_notes=raw_notes,
        structured=structured,
        created_by_user_id=user.id if user else None,
    )
    session.add(intake)
    session.commit()

    for step in structured.get("next_steps", []) or []:
        handler = step.get("handler", "human_review")
        auto_executable = bool(step.get("auto_executable")) and handler != "human_review"
        item = actions.create_action_item(
            session,
            title=step.get("title", "Follow up"),
            description=step.get("description", "") or step.get("payload_hint", ""),
            category=step.get("category", "intake_followup"),
            handler=handler if auto_executable else "",
            payload={"lead_id": lead.id, "client_id": client.id, "hint": step.get("payload_hint", "")},
            auto_executable=auto_executable,
            lead_id=lead.id,
            client_id=client.id,
            created_by="agent:concierge",
        )
        if auto_executable:
            actions.execute_action_item(session, settings, item.id)

    logger.info("Intake analyzed for lead %s -> client %s", lead.id, client.id)
    return intake


def list_intakes(session: Session) -> list[IntakeCall]:
    return session.query(IntakeCall).order_by(IntakeCall.created_at.desc()).all()
