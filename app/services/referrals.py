"""The Constellation agent: manual §11's referral engine. Detects when a
client's 90-day review is due, drafts the ask script + forwardable intro
message so the actual conversation is easy to have (holding it stays a human
step — this only removes "what do I even say"), and tracks the outcome
funnel that feeds the referral-share KPI (manual §15).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.claude import BusinessProfile, ClaudeDrafter
from app.models import (
    ActionCategory,
    ActionItem,
    Client,
    ClientStatus,
    ReferralRecord,
    ReferralStatus,
)
from app.services import actions

logger = logging.getLogger(__name__)

_REVIEW_WINDOW_DAYS = 90


def _business_profile(settings: Settings) -> BusinessProfile:
    return BusinessProfile(
        business_name=settings.business_name,
        business_pitch=settings.business_pitch,
        sender_name=settings.sender_name,
        sender_title=settings.sender_title,
        calendly_link=settings.calendly_booking_link,
        physical_address=settings.business_physical_address,
    )


def check_referral_reviews_due(session: Session, settings: Settings) -> dict[str, int]:
    stats = {"reminders_created": 0}
    cutoff = datetime.now(timezone.utc) - timedelta(days=_REVIEW_WINDOW_DAYS)
    clients = (
        session.query(Client)
        .filter(
            Client.status == ClientStatus.ACTIVE.value,
            Client.start_date.is_not(None),
            Client.start_date <= cutoff,
        )
        .all()
    )

    for client in clients:
        existing = (
            session.query(ActionItem)
            .filter(ActionItem.client_id == client.id, ActionItem.category == ActionCategory.REFERRAL.value)
            .first()
        )
        if existing is not None:
            continue  # already asked once for this client

        profile = _business_profile(settings)
        drafter = ClaudeDrafter(settings.anthropic_api_key, settings.anthropic_model)
        case_study_numbers = client.notes.strip() or "results not yet logged for this client"
        draft = drafter.draft_referral_ask(
            {"company_name": client.company_name, "contact_name": client.contact_name},
            case_study_numbers,
            profile,
        )
        result_text = (
            f"Ask script:\n{draft['ask_script']}\n\n"
            f"Forwardable intro message:\n{draft['forwardable_message']}\n\n"
            "Log the outcome on the Referrals page once you've had the conversation."
        )
        item = actions.create_action_item(
            session,
            title=f"Hold the 90-day review + ask for referrals — {client.company_name}",
            description=(
                "Manual §11: ask for two names, not 'anyone you know'. The script "
                "below is drafted; mark this done once you've actually had the review."
            ),
            category=ActionCategory.REFERRAL.value,
            auto_executable=True,
            client_id=client.id,
            created_by="agent:constellation",
        )
        item.result = result_text
        session.commit()
        stats["reminders_created"] += 1

    logger.info("Referral review check complete: %s", stats)
    return stats


def log_referral_outcome(
    session: Session,
    *,
    client_id: int,
    referrer_name: str,
    referred_name: str,
    referred_email: str = "",
    status: str = ReferralStatus.ASKED.value,
    notes: str = "",
) -> ReferralRecord:
    record = ReferralRecord(
        client_id=client_id,
        referrer_name=referrer_name,
        referred_name=referred_name,
        referred_email=referred_email,
        status=status,
        notes=notes,
    )
    if status == ReferralStatus.CLOSED.value:
        record.closed_at = datetime.now(timezone.utc)
    session.add(record)
    session.commit()
    return record


def update_referral_status(session: Session, referral_id: int, status: str) -> ReferralRecord:
    record = session.get(ReferralRecord, referral_id)
    if record is None:
        raise ValueError(f"No referral record {referral_id}")
    record.status = status
    if status == ReferralStatus.CLOSED.value:
        record.closed_at = datetime.now(timezone.utc)
    session.commit()
    return record


def list_referrals(session: Session) -> list[ReferralRecord]:
    return session.query(ReferralRecord).order_by(ReferralRecord.created_at.desc()).all()
