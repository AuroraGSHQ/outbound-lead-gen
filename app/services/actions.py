"""The general task/agent-action queue: every next-step any agent proposes
lands here as an ActionItem, whether the system can execute it or a person
has to (`auto_executable`). `execute_action_item` dispatches on `handler` —
auto_executable only means "a handler exists"; anything client-facing still
lands in the existing Message approval queue rather than sending on its own,
the same rule as the rest of the app (see services/approvals.py).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.claude import BusinessProfile, ClaudeDrafter
from app.models import (
    ActionItem,
    ActionStatus,
    Client,
    Conversation,
    Lead,
    Message,
    MessageDirection,
    MessageStatus,
)

logger = logging.getLogger(__name__)


def create_action_item(
    session: Session,
    *,
    title: str,
    category: str,
    description: str = "",
    handler: str = "",
    payload: dict | None = None,
    auto_executable: bool = False,
    lead_id: int | None = None,
    client_id: int | None = None,
    assignee_user_id: int | None = None,
    created_by: str = "",
    due_at: datetime | None = None,
) -> ActionItem:
    item = ActionItem(
        title=title,
        description=description,
        category=category,
        handler=handler,
        payload=payload or {},
        auto_executable=auto_executable,
        lead_id=lead_id,
        client_id=client_id,
        assignee_user_id=assignee_user_id,
        created_by=created_by,
        due_at=due_at,
    )
    session.add(item)
    session.commit()
    return item


def _business_profile(settings: Settings) -> BusinessProfile:
    return BusinessProfile(
        business_name=settings.business_name,
        business_pitch=settings.business_pitch,
        sender_name=settings.sender_name,
        sender_title=settings.sender_title,
        calendly_link=settings.calendly_booking_link,
        physical_address=settings.business_physical_address,
    )


def _queue_client_email(
    session: Session, settings: Settings, lead: Lead, purpose: str, context: str
) -> str:
    profile = _business_profile(settings)
    drafter = ClaudeDrafter(settings.anthropic_api_key, settings.anthropic_model)
    draft = drafter.draft_client_email(
        purpose,
        {"contact_name": lead.contact_name, "company_name": lead.company_name},
        context,
        profile,
    )
    from app.services.approvals import compose_footer

    greeting = f"Hi {lead.contact_name.split(' ')[0]}," if lead.contact_name else "Hi,"
    body = (
        f"{greeting}\n\n{draft['body']}\n\nBest,\n{profile.sender_name}\n{profile.sender_title}"
        f"\n\n{compose_footer(profile)}"
    )
    conversation = Conversation(lead_id=lead.id, gmail_thread_id="", autopilot=False)
    session.add(conversation)
    session.flush()
    message = Message(
        conversation_id=conversation.id,
        direction=MessageDirection.OUTBOUND.value,
        subject=draft.get("subject") or purpose,
        body=body,
        status=MessageStatus.PENDING_APPROVAL.value,
    )
    session.add(message)
    session.commit()
    return f"Drafted and queued for approval: message #{message.id}."


def _handle_schedule_90day_review_reminder(
    session: Session, settings: Settings, item: ActionItem
) -> str:
    client_id = item.payload.get("client_id") or item.client_id
    client = session.get(Client, client_id) if client_id else None
    if client and client.start_date:
        when = client.start_date + timedelta(days=90)
    else:
        when = datetime.now(timezone.utc) + timedelta(days=90)
    item.due_at = when
    return f"90-day review reminder set for {when.date().isoformat()}."


def _handle_draft_welcome_email(session: Session, settings: Settings, item: ActionItem) -> str:
    lead_id = item.payload.get("lead_id") or item.lead_id
    lead = session.get(Lead, lead_id) if lead_id else None
    if lead is None:
        return "No lead attached to this action item — skipped."
    return _queue_client_email(
        session, settings, lead, "welcome / onboarding", item.description or item.title
    )


def _handle_draft_proposal_email(session: Session, settings: Settings, item: ActionItem) -> str:
    lead_id = item.payload.get("lead_id") or item.lead_id
    lead = session.get(Lead, lead_id) if lead_id else None
    if lead is None:
        return "No lead attached to this action item — skipped."
    return _queue_client_email(
        session, settings, lead, "proposal follow-up", item.description or item.title
    )


def _handle_run_scanner_scan(session: Session, settings: Settings, item: ActionItem) -> str:
    domain = item.payload.get("domain")
    if not domain:
        return "No domain in payload — skipped."
    from app.services import scanner

    target = scanner.queue_target(session, domain=domain, company_name=item.payload.get("company_name", ""))
    scanner.run_scan(session, target.id)
    return f"Scan complete for {domain} — see /scanner/{target.id}."


def _handle_add_to_referral_program(session: Session, settings: Settings, item: ActionItem) -> str:
    client_id = item.payload.get("client_id") or item.client_id
    client = session.get(Client, client_id) if client_id else None
    if client is None:
        return "No client attached to this action item — skipped."
    note = "Enrolled in the referral program (10% of first-year fees)."
    client.notes = f"{client.notes}\n{note}".strip()
    return note


_HANDLERS: dict[str, Callable[[Session, Settings, ActionItem], str]] = {
    "schedule_90day_review_reminder": _handle_schedule_90day_review_reminder,
    "draft_welcome_email": _handle_draft_welcome_email,
    "draft_proposal_email": _handle_draft_proposal_email,
    "run_scanner_scan": _handle_run_scanner_scan,
    "add_to_referral_program": _handle_add_to_referral_program,
    # "human_review" is intentionally absent — it always requires a person.
}


def execute_action_item(session: Session, settings: Settings, action_item_id: int) -> ActionItem:
    item = session.get(ActionItem, action_item_id)
    if item is None:
        raise ValueError(f"No action item {action_item_id}")
    handler_fn = _HANDLERS.get(item.handler)
    if handler_fn is None:
        raise ValueError(
            f"No automated handler for '{item.handler}' — this action needs a person; "
            "use mark_done() once it's handled."
        )
    try:
        result = handler_fn(session, settings, item)
        item.result = result
        item.status = ActionStatus.DONE.value
    except Exception as exc:  # noqa: BLE001 - surface the failure on the item, don't crash the caller
        logger.exception("Action item %s handler failed", item.id)
        item.result = f"Failed: {exc}"
        item.status = ActionStatus.PROPOSED.value
    session.commit()
    return item


def mark_done(session: Session, action_item_id: int, note: str = "") -> ActionItem:
    item = session.get(ActionItem, action_item_id)
    if item is None:
        raise ValueError(f"No action item {action_item_id}")
    item.status = ActionStatus.DONE.value
    if note:
        item.result = note
    session.commit()
    return item


def assign(session: Session, action_item_id: int, assignee_user_id: int | None) -> ActionItem:
    item = session.get(ActionItem, action_item_id)
    if item is None:
        raise ValueError(f"No action item {action_item_id}")
    item.assignee_user_id = assignee_user_id
    session.commit()
    return item


def list_action_items(
    session: Session, *, status: str | None = None, category: str | None = None
) -> list[ActionItem]:
    query = session.query(ActionItem)
    if status:
        query = query.filter(ActionItem.status == status)
    if category:
        query = query.filter(ActionItem.category == category)
    return query.order_by(ActionItem.created_at.desc()).all()
