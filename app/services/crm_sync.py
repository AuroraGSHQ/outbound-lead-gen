"""Charon — CRM handoff. Parses pasted lead/client details into structured
contact fields, then pushes them into whichever CRM that specific client
uses. Each client's CRM connection (provider, API key, board/location id,
column mapping) is configured once on the client record — nothing here
guesses at credentials or reuses another client's connection, and an
unconfigured client fails loudly rather than silently dropping the lead.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations import crm_highlevel, crm_hubspot, crm_monday
from app.integrations.claude import ClaudeDrafter
from app.models import Client, CrmProvider, CrmSyncLog, CrmSyncStatus

logger = logging.getLogger(__name__)


def parse_lead_details(settings: Settings, raw_text: str) -> dict[str, str]:
    drafter = ClaudeDrafter(settings.anthropic_api_key, settings.anthropic_model)
    return drafter.parse_lead_details(raw_text)


def push_to_crm(
    session: Session, client_id: int, fields: dict, *, created_by_user_id: int | None = None
) -> CrmSyncLog:
    client = session.get(Client, client_id)
    if client is None:
        raise ValueError(f"No client {client_id}")

    summary = fields.get("full_name") or fields.get("email") or fields.get("company") or "unnamed contact"
    provider = client.crm_provider or CrmProvider.NONE.value
    config = client.crm_config or {}

    try:
        if provider == CrmProvider.NONE.value:
            raise ValueError(f"{client.company_name} has no CRM connection configured yet.")
        elif provider == CrmProvider.HUBSPOT.value:
            crm_hubspot.upsert_contact(client.crm_api_key, fields)
        elif provider == CrmProvider.MONDAY.value:
            crm_monday.create_item(
                client.crm_api_key, config.get("board_id", ""), config.get("column_map", {}), fields
            )
        elif provider == CrmProvider.HIGHLEVEL.value:
            crm_highlevel.upsert_contact(client.crm_api_key, config.get("location_id", ""), fields)
        else:
            raise ValueError(
                f"No built-in integration for provider '{provider}' — log this lead into "
                f"{client.company_name}'s CRM by hand for now."
            )
        status = CrmSyncStatus.SUCCESS.value
        message = "Synced."
    except Exception as exc:  # noqa: BLE001 - surfaced on the log row, never crashes the caller
        logger.exception("CRM sync failed for client %s", client.id)
        status = CrmSyncStatus.ERROR.value
        message = str(exc)

    log = CrmSyncLog(
        client_id=client.id,
        provider=provider,
        status=status,
        contact_summary=summary,
        message=message,
        created_by_user_id=created_by_user_id,
    )
    session.add(log)
    session.commit()
    return log


def list_logs(session: Session, *, client_id: int | None = None) -> list[CrmSyncLog]:
    query = session.query(CrmSyncLog)
    if client_id:
        query = query.filter(CrmSyncLog.client_id == client_id)
    return query.order_by(CrmSyncLog.created_at.desc()).all()
