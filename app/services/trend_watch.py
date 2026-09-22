"""Comet — trend detection. Scans the recent MetricSnapshot history for a
metric moving the same direction several snapshots running and raises a
flag once it crosses that threshold — a smoke detector, not an analyst;
Observatory (services/metrics.py, services/content.py) is where the real
numbers and narrative live. On-demand only.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models import ActionCategory, ActionItem, ActionStatus
from app.services import actions, metrics

logger = logging.getLogger(__name__)

_DEFAULT_LOOKBACK = 3  # consecutive snapshots required to call it a trend


def _direction(values: list[float]) -> str | None:
    if len(values) < 2:
        return None
    if all(values[i] < values[i + 1] for i in range(len(values) - 1)):
        return "up"
    if all(values[i] > values[i + 1] for i in range(len(values) - 1)):
        return "down"
    return None


def check_trend(session: Session, settings=None, *, lookback: int = _DEFAULT_LOOKBACK) -> dict[str, int]:
    stats = {"trends_flagged": 0}
    history = metrics.snapshot_history(session, limit=lookback)
    if len(history) < lookback:
        logger.info("Not enough snapshots yet (%s/%s) — nothing to trend.", len(history), lookback)
        return stats

    # snapshot_history comes back newest-first; walk it oldest-to-newest.
    ordered = list(reversed(history))
    values = [s.qualified_conversations for s in ordered]
    direction = _direction(values)
    if direction is None:
        return stats

    title = f"Trend — qualified conversations trending {direction} for {lookback} snapshots running"
    existing = (
        session.query(ActionItem)
        .filter(ActionItem.title == title, ActionItem.status != ActionStatus.DONE.value)
        .first()
    )
    if existing is not None:
        return stats

    actions.create_action_item(
        session,
        title=title,
        description=(
            f"Qualified conversations have moved {direction} for {lookback} snapshots in a "
            f"row ({', '.join(str(v) for v in values)}). Worth a look at what changed."
        ),
        category=ActionCategory.CONTENT.value,
        created_by="agent:comet",
    )
    stats["trends_flagged"] = 1
    logger.info("Trend check complete: %s", stats)
    return stats
