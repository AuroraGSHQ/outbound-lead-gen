"""Chronos — the monthly ops rollup. Distinct from Iris's (Courier) daily
digest on purpose: a once-a-month management view of what's aging on the
board, so the two can't duplicate each other's notifications.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import ActionItem, ActionStatus, ScanResult
from app.services.notify import notify_owner_now

logger = logging.getLogger(__name__)


def send_monthly_rollup(session: Session, settings) -> dict[str, int]:
    open_items = session.query(ActionItem).filter(ActionItem.status != ActionStatus.DONE.value).all()
    by_category: dict[str, int] = {}
    stale_cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    overdue = [i for i in open_items if i.created_at <= stale_cutoff]
    for item in open_items:
        by_category[item.category] = by_category.get(item.category, 0) + 1

    stale_scans = (
        session.query(ScanResult)
        .filter(ScanResult.verified.is_(False), ScanResult.checked_at <= stale_cutoff)
        .count()
    )

    lines = ["Monthly ops rollup:\n", f"Open action items: {len(open_items)} ({len(overdue)} older than 2 weeks)\n"]
    for category, count in sorted(by_category.items(), key=lambda kv: -kv[1]):
        lines.append(f"  - {category}: {count}")
    lines.append(f"\nScans still awaiting verification (14+ days): {stale_scans}")
    if overdue:
        lines.append("\nOldest open items:")
        for item in sorted(overdue, key=lambda i: i.created_at)[:10]:
            lines.append(f"  - [{item.category}] {item.title} (opened {item.created_at.date().isoformat()})")

    notify_owner_now(settings, "Olympus: monthly ops rollup", "\n".join(lines))
    return {"open_items": len(open_items), "overdue": len(overdue), "stale_scans": stale_scans}
