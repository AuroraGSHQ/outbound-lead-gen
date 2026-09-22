"""Sentinel — deliverability watch. The hard ceiling on how fast outbound can
scale is sending-address reputation (see docs/SETUP.md's "Sizing your
outreach volume"), and it's the one thing nothing was watching. Sentinel
checks send-volume trend and whether Beacon's Gmail token is
actually usable — and only ever raises a flag. It never pauses sending or
touches DAILY_OUTREACH_CAP itself.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import ActionCategory, ActionItem, Message, MessageDirection, MessageStatus
from app.services import actions

logger = logging.getLogger(__name__)

_SPIKE_THRESHOLD = 1.3  # 30% day-over-day jump in sends is worth a look


def _sent_count_on(session: Session, day_start: datetime) -> int:
    day_end = day_start + timedelta(days=1)
    return (
        session.query(Message)
        .filter(
            Message.direction == MessageDirection.OUTBOUND.value,
            Message.status == MessageStatus.SENT.value,
            Message.sent_at >= day_start,
            Message.sent_at < day_end,
        )
        .count()
    )


def _flag_once(session: Session, title: str, description: str) -> bool:
    if session.query(ActionItem).filter(ActionItem.title == title, ActionItem.status != "done").first():
        return False
    actions.create_action_item(
        session, title=title, description=description, category=ActionCategory.SYSTEM.value, created_by="agent:sentinel"
    )
    return True


def check_deliverability(session: Session, settings: Settings) -> dict[str, int]:
    stats = {"flags_raised": 0}
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_count = _sent_count_on(session, today_start)
    yesterday_count = _sent_count_on(session, today_start - timedelta(days=1))

    if yesterday_count >= 5 and today_count >= yesterday_count * _SPIKE_THRESHOLD:
        if _flag_once(
            session,
            "Send-volume spike detected",
            f"{yesterday_count} sent yesterday, {today_count} so far today — a jump like this "
            "on a real Gmail/Workspace address risks a spam flag. Worth checking before it "
            "compounds.",
        ):
            stats["flags_raised"] += 1

    if today_count >= settings.daily_outreach_cap:
        if _flag_once(
            session,
            "Sending at the daily outreach cap",
            f"Hit DAILY_OUTREACH_CAP ({settings.daily_outreach_cap}) today. If this is happening "
            "regularly, ramp the cap up gradually rather than jumping — see docs/SETUP.md.",
        ):
            stats["flags_raised"] += 1

    token_path = Path(settings.gmail_token_path)
    if settings.gmail_sender_email and not token_path.exists():
        if _flag_once(
            session,
            "Gmail token missing",
            f"{token_path} not found — sending/polling will fail until scripts/gmail_auth.py is re-run.",
        ):
            stats["flags_raised"] += 1

    logger.info("Deliverability check complete: %s", stats)
    return stats
