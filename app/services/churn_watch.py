"""Collision — churn early-warning. Flags an active client going quiet
before it shows up as a missed payment. Raises a flag only — Equinox
(billing) or a human is still the one who ever moves Client.status to
paused/churned, so the two can't race to change the same field.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import ActionCategory, ActionItem, ActionStatus, Client, ClientStatus
from app.services import actions

logger = logging.getLogger(__name__)

_QUIET_AFTER_DAYS = 60


def check_churn_risk(session: Session, settings=None) -> dict[str, int]:
    stats = {"flags_raised": 0}
    cutoff = datetime.now(timezone.utc) - timedelta(days=_QUIET_AFTER_DAYS)

    active_clients = session.query(Client).filter(Client.status == ClientStatus.ACTIVE.value).all()
    for client in active_clients:
        last_activity = (
            session.query(ActionItem)
            .filter(ActionItem.client_id == client.id)
            .order_by(ActionItem.created_at.desc())
            .first()
        )
        last_touch = last_activity.created_at if last_activity else client.created_at
        if last_touch > cutoff:
            continue

        title = f"Churn risk — {client.company_name} has gone quiet"
        existing = (
            session.query(ActionItem)
            .filter(ActionItem.title == title, ActionItem.status != ActionStatus.DONE.value)
            .first()
        )
        if existing is not None:
            continue

        actions.create_action_item(
            session,
            title=title,
            description=f"No recorded activity for this client since {last_touch.date().isoformat()}.",
            category=ActionCategory.CHURN.value,
            client_id=client.id,
            created_by="agent:collision",
        )
        stats["flags_raised"] += 1

    logger.info("Churn risk check complete: %s", stats)
    return stats
