"""Plutus — billing & invoicing. No payment processor is wired in (Stripe/
QuickBooks/etc. would be the real integration point); this is the honest
version: a monthly reminder task per active client, and an escalation if
last month's still isn't marked done. Never drafts anything client-facing —
sending the actual invoice is a human/accounting-tool step.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import ActionCategory, ActionItem, ActionStatus, Client, ClientStatus
from app.services import actions

logger = logging.getLogger(__name__)


def _month_label(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


def run_billing_sweep(session: Session, settings=None) -> dict[str, int]:
    stats = {"invoices_flagged": 0, "overdue_flagged": 0}
    now = datetime.now(timezone.utc)
    this_month = _month_label(now)

    active_clients = (
        session.query(Client).filter(Client.status == ClientStatus.ACTIVE.value, Client.monthly_fee > 0).all()
    )
    for client in active_clients:
        title = f"Invoice due — {client.company_name} ({this_month})"
        exists = session.query(ActionItem).filter(ActionItem.title == title).first()
        if exists is None:
            actions.create_action_item(
                session,
                title=title,
                description=f"${client.monthly_fee:,.2f} due for {this_month}.",
                category=ActionCategory.BILLING.value,
                client_id=client.id,
                created_by="agent:plutus",
            )
            stats["invoices_flagged"] += 1

        overdue = (
            session.query(ActionItem)
            .filter(
                ActionItem.client_id == client.id,
                ActionItem.category == ActionCategory.BILLING.value,
                ActionItem.status != ActionStatus.DONE.value,
                ActionItem.title != title,
            )
            .first()
        )
        if overdue is not None:
            escalation_title = f"Overdue — {client.company_name} hasn't confirmed a prior invoice"
            already_escalated = session.query(ActionItem).filter(ActionItem.title == escalation_title).first()
            if already_escalated is None:
                actions.create_action_item(
                    session,
                    title=escalation_title,
                    description=f"'{overdue.title}' is still open. Check payment status before it slips further.",
                    category=ActionCategory.BILLING.value,
                    client_id=client.id,
                    created_by="agent:plutus",
                )
                stats["overdue_flagged"] += 1

    logger.info("Billing sweep complete: %s", stats)
    return stats
