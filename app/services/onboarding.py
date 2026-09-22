"""Launchpad — client onboarding & delivery. Once a client goes active, this
plants the standard onboarding checklist as ActionItems (category=onboarding)
so nothing about getting them live gets forgotten. Picks up exactly where
Horizon (intake) leaves off — it never touches a lead that hasn't signed.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import ActionCategory, ActionItem, Client, ClientStatus
from app.services import actions

logger = logging.getLogger(__name__)

_CHECKLIST = [
    "Kickoff call scheduled",
    "Tracking (calls/forms) installed and verified",
    "First campaign/sequence launched",
    "Booking flow tested end-to-end with the client watching",
    "30-day check-in scheduled",
]


def has_onboarding_checklist(session: Session, client_id: int) -> bool:
    return (
        session.query(ActionItem)
        .filter(ActionItem.client_id == client_id, ActionItem.category == ActionCategory.ONBOARDING.value)
        .first()
        is not None
    )


def create_onboarding_checklist(session: Session, client: Client) -> list[ActionItem]:
    if has_onboarding_checklist(session, client.id):
        return []
    items = []
    for i, title in enumerate(_CHECKLIST):
        due = datetime.now(timezone.utc) + timedelta(days=2 + i * 3)
        item = actions.create_action_item(
            session,
            title=f"Onboarding — {client.company_name}: {title}",
            category=ActionCategory.ONBOARDING.value,
            client_id=client.id,
            created_by="agent:launchpad",
            due_at=due,
        )
        items.append(item)
    logger.info("Onboarding checklist created for client %s (%d items)", client.id, len(items))
    return items


def run_onboarding_sweep(session: Session, settings=None) -> dict[str, int]:
    """Safety net for the scheduled run: catches any client that went
    active without the checklist being created inline (e.g. seeded via a
    script or a bulk update)."""
    stats = {"checklists_created": 0}
    active_clients = session.query(Client).filter(Client.status == ClientStatus.ACTIVE.value).all()
    for client in active_clients:
        if not has_onboarding_checklist(session, client.id):
            create_onboarding_checklist(session, client)
            stats["checklists_created"] += 1
    return stats
