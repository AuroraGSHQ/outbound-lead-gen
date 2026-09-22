"""Quasar — high-impact campaign opportunities. A lighter-weight "opportunity
spotter", not a full campaign builder: reads the latest MetricSnapshot's
close-rate-by-channel and flags the single best-performing channel as worth
more budget, so it's easy to review, and doesn't fire again on the same
finding. On-demand only, run from the Team page or triggered after a fresh
metrics snapshot — no separate scheduler job.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models import ActionCategory, ActionItem, ActionStatus
from app.services import actions, metrics

logger = logging.getLogger(__name__)

_MIN_CLOSE_RATE = 0.0  # any positive close rate is worth surfacing


def spot_campaign_opportunities(session: Session, settings=None) -> dict[str, int]:
    stats = {"opportunities_flagged": 0}
    snapshot = metrics.latest_snapshot(session)
    if snapshot is None or not snapshot.close_rate:
        logger.info("No metric snapshot with close-rate data yet — nothing to spot.")
        return stats

    best_channel, best_rate = max(snapshot.close_rate.items(), key=lambda kv: kv[1])
    if best_rate <= _MIN_CLOSE_RATE:
        return stats

    title = f"High-leverage opportunity — double down on {best_channel}"
    existing = (
        session.query(ActionItem)
        .filter(ActionItem.title == title, ActionItem.status != ActionStatus.DONE.value)
        .first()
    )
    if existing is not None:
        return stats

    cost = (snapshot.cost_per_qualified_conversation or {}).get(best_channel)
    cost_line = f" at ~${cost:.0f} per qualified conversation" if cost else ""
    actions.create_action_item(
        session,
        title=title,
        description=(
            f"{best_channel} is closing at {best_rate:.0%} this month{cost_line} — the best "
            "close rate of any channel in the latest snapshot. Worth a look at shifting "
            "budget or ad slots toward it next quarter."
        ),
        category=ActionCategory.ADS.value,
        created_by="agent:quasar",
    )
    stats["opportunities_flagged"] = 1
    logger.info("Campaign opportunity check complete: %s", stats)
    return stats
