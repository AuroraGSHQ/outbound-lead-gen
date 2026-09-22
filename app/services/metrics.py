"""The Observatory agent's measurement half: manual §15's six numbers, computed
from whatever real data the app actually has (no invented attribution) and
stored as a daily MetricSnapshot so /metrics doesn't recompute on every
load. Where there isn't enough data to say something honest — e.g. no
conversions attributed to a channel yet — the field is left out rather than
padded, matching the manual's own "honesty is the whole value" rule.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import AdCampaign, Client, ClientStatus, Meeting, MetricSnapshot
from app.services import ads

logger = logging.getLogger(__name__)


def _sum_budget_by_platform(campaigns: list[AdCampaign]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for c in campaigns:
        totals[c.platform] = totals.get(c.platform, 0.0) + (c.budget_monthly or 0.0)
    return totals


def compute_and_store_snapshot(session: Session) -> MetricSnapshot:
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    meetings_this_month = session.query(Meeting).filter(Meeting.created_at >= month_start).all()
    qualified_conversations = len(meetings_this_month)

    campaigns = (
        session.query(AdCampaign).filter(AdCampaign.quarter_label == ads.current_quarter_label()).all()
    )
    spend_by_platform = _sum_budget_by_platform(campaigns)
    ads_meetings = [m for m in meetings_this_month if m.lead and m.lead.source == "ads"]
    cost_per_conversation: dict[str, float] = {}
    if ads_meetings:
        for platform, spend in spend_by_platform.items():
            cost_per_conversation[platform] = round(spend / len(ads_meetings), 2)

    clients_this_month = (
        session.query(Client)
        .filter(Client.start_date >= month_start, Client.status != ClientStatus.PROSPECT.value)
        .all()
    )

    by_source_meetings: dict[str, int] = {}
    for m in meetings_this_month:
        src = (m.lead.source if m.lead else "unknown") or "unknown"
        by_source_meetings[src] = by_source_meetings.get(src, 0) + 1
    by_source_clients: dict[str, int] = {}
    for c in clients_this_month:
        src = (c.lead.source if c.lead else "unknown") or "unknown"
        by_source_clients[src] = by_source_clients.get(src, 0) + 1
    close_rate = {
        src: round(by_source_clients.get(src, 0) / count, 2)
        for src, count in by_source_meetings.items()
        if count
    }

    total_spend = sum(spend_by_platform.values())
    cost_per_closed_client = (
        round(total_spend / len(clients_this_month), 2) if clients_this_month and total_spend else None
    )

    close_times = [
        (c.start_date - c.lead.created_at).days
        for c in clients_this_month
        if c.lead and c.lead.created_at and c.start_date
    ]
    avg_time_to_close_days = round(sum(close_times) / len(close_times), 1) if close_times else None

    referral_clients = sum(1 for c in clients_this_month if c.referral_source)
    referral_share = round(referral_clients / len(clients_this_month), 2) if clients_this_month else None

    snapshot = MetricSnapshot(
        date=now,
        qualified_conversations=qualified_conversations,
        cost_per_qualified_conversation=cost_per_conversation,
        close_rate=close_rate,
        cost_per_closed_client=cost_per_closed_client,
        avg_time_to_close_days=avg_time_to_close_days,
        referral_share=referral_share,
    )
    session.add(snapshot)
    session.commit()
    logger.info("Metric snapshot computed: %s qualified conversations this month.", qualified_conversations)
    return snapshot


def latest_snapshot(session: Session) -> MetricSnapshot | None:
    return session.query(MetricSnapshot).order_by(MetricSnapshot.date.desc()).first()


def snapshot_history(session: Session, limit: int = 30) -> list[MetricSnapshot]:
    return session.query(MetricSnapshot).order_by(MetricSnapshot.date.desc()).limit(limit).all()
