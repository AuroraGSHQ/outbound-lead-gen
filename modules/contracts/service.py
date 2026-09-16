"""Business logic for the contracts module: create-on-close, the
approve/reject gate, and the signed-webhook/poll callback.

No FastAPI here — router.py and jobs.py call into this, this module never
imports either of them. Keeps the "who owns the send" invariant enforced in
one place: nothing outside `approve_contract` may call a provider's
send-for-signature method.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text

from app.config import get_settings
from modules.common.db import db_conn
from modules.common.notifications import create_notification
from modules.contracts.docusign_client import DocuSignClient
from modules.contracts.pandadoc_client import PandaDocClient

logger = logging.getLogger(__name__)

_TIMESTAMP_SQL = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"


class ContractNotFoundError(Exception):
    pass


class ContractStateError(Exception):
    """Raised when an action is attempted against a contract that isn't in
    the state it requires (e.g. approving something already sent)."""


# ----------------------------------------------------------------------
# Provider dispatch — the only place service.py knows PandaDoc/DocuSign
# have different call shapes. Everything above this line is provider-
# agnostic.
# ----------------------------------------------------------------------
def _pandadoc_client(settings) -> PandaDocClient:
    return PandaDocClient(api_key=settings.pandadoc_api_key)


def _docusign_client(settings) -> DocuSignClient:
    return DocuSignClient(
        integration_key=settings.docusign_integration_key,
        user_id=settings.docusign_user_id,
        account_id=settings.docusign_account_id,
        private_key_path=settings.docusign_private_key_path,
        base_url=settings.docusign_base_url,
    )


def _split_name(full_name: str) -> tuple[str, str]:
    first, _, last = (full_name or "").strip().partition(" ")
    return first, last


def create_document(
    settings,
    *,
    template_id: str,
    filled_data: dict[str, Any],
    recipient_email: str,
    recipient_name: str,
) -> str:
    """Create a filled-but-unsent document/envelope. Returns the provider's
    external document/envelope id as a string.
    """
    provider = settings.contracts_provider
    if provider == "pandadoc":
        client = _pandadoc_client(settings)
        try:
            first, last = _split_name(recipient_name)
            resp = client.create_document_from_template(
                template_id=template_id,
                name=f"Contract - {recipient_name or recipient_email}",
                filled_data=filled_data,
                recipient_email=recipient_email,
                recipient_first_name=first,
                recipient_last_name=last,
            )
        finally:
            client.close()
        return str(resp["id"])
    elif provider == "docusign":
        client = _docusign_client(settings)
        try:
            resp = client.create_envelope_from_template(
                template_id=template_id,
                filled_data=filled_data,
                recipient_email=recipient_email,
                recipient_name=recipient_name,
            )
        finally:
            client.close()
        return str(resp["envelopeId"])
    else:
        raise ValueError(f"Unknown contracts_provider: {provider!r}")


def send_document(settings, *, provider: str, external_document_id: str) -> None:
    """The one function in this module that is allowed to call a provider's
    send-for-signature endpoint. Only `approve_contract` calls this.
    """
    if provider == "pandadoc":
        client = _pandadoc_client(settings)
        try:
            client.send_document(external_document_id)
        finally:
            client.close()
    elif provider == "docusign":
        client = _docusign_client(settings)
        try:
            client.send_envelope(external_document_id)
        finally:
            client.close()
    else:
        raise ValueError(f"Unknown contracts_provider: {provider!r}")


def fetch_status_and_check_signed(settings, *, provider: str, external_document_id: str) -> bool:
    """Poll the provider for a document/envelope's current status. Returns
    True if the provider now considers it fully signed/completed.
    """
    if provider == "pandadoc":
        client = _pandadoc_client(settings)
        try:
            payload = client.get_document(external_document_id)
            return client.is_signed(payload)
        finally:
            client.close()
    elif provider == "docusign":
        client = _docusign_client(settings)
        try:
            payload = client.get_envelope(external_document_id)
            return client.is_signed(payload)
        finally:
            client.close()
    else:
        raise ValueError(f"Unknown contracts_provider: {provider!r}")


# ----------------------------------------------------------------------
# Public service functions
# ----------------------------------------------------------------------
def create_contract_for_closed_deal(
    contact_id: int, call_log_id: int | None, deal_fields: dict[str, Any]
) -> int:
    """Pull the contact's fields + caller-supplied deal_fields (price,
    scope, deadline, ...) into `filled_data`, create the filled-but-unsent
    document with the configured provider, insert the `contracts` row, and
    fire a `contract_needs_approval` notification. Returns the new
    contracts.id.
    """
    settings = get_settings()

    with db_conn() as conn:
        contact = conn.execute(
            text(
                "SELECT id, name, phone, email, company_name, segment, service_line "
                "FROM contacts WHERE id = :id"
            ),
            {"id": contact_id},
        ).mappings().first()
        if contact is None:
            raise ContractNotFoundError(f"No contact with id={contact_id}")

        filled_data: dict[str, Any] = {
            "contact_name": contact["name"],
            "contact_email": contact["email"],
            "contact_phone": contact["phone"],
            "company_name": contact["company_name"],
            **deal_fields,
        }

        template_id = settings.contract_template_id
        external_document_id = create_document(
            settings,
            template_id=template_id,
            filled_data=filled_data,
            recipient_email=contact["email"],
            recipient_name=contact["name"],
        )

        result = conn.execute(
            text(
                """
                INSERT INTO contracts
                    (contact_id, call_log_id, template_id, provider, external_document_id,
                     filled_data, status)
                VALUES
                    (:contact_id, :call_log_id, :template_id, :provider, :external_document_id,
                     :filled_data, 'pending_approval')
                """
            ),
            {
                "contact_id": contact_id,
                "call_log_id": call_log_id,
                "template_id": template_id,
                "provider": settings.contracts_provider,
                "external_document_id": external_document_id,
                "filled_data": json.dumps(filled_data),
            },
        )
        contract_id = result.lastrowid

        create_notification(
            "contract_needs_approval",
            title="Contract ready for your approval",
            body=f"A contract for {contact['name'] or contact['email'] or 'a contact'} is filled and waiting on your review before it's sent for signature.",
            payload={"contract_id": contract_id, "contact_id": contact_id},
            contact_id=contact_id,
            conn=conn,
        )

        return contract_id


def _get_contract(conn, contract_id: int) -> dict[str, Any]:
    row = conn.execute(
        text("SELECT * FROM contracts WHERE id = :id"), {"id": contract_id}
    ).mappings().first()
    if row is None:
        raise ContractNotFoundError(f"No contract with id={contract_id}")
    return dict(row)


def approve_contract(contract_id: int) -> None:
    """Approve a pending contract and send it for signature.

    Must currently be status == 'pending_approval'. Moves to 'approved'
    first (committed) so a send failure doesn't silently leave the row
    looking untouched, then calls the provider's send API, then moves to
    'sent'.
    """
    settings = get_settings()

    with db_conn() as conn:
        contract = _get_contract(conn, contract_id)
        if contract["status"] != "pending_approval":
            raise ContractStateError(
                f"Contract {contract_id} is '{contract['status']}', not 'pending_approval'"
            )
        conn.execute(
            text(
                f"UPDATE contracts SET status = 'approved', updated_at = {_TIMESTAMP_SQL} "
                "WHERE id = :id"
            ),
            {"id": contract_id},
        )
        provider = contract["provider"]
        external_document_id = contract["external_document_id"]

    # Outside the transaction on purpose: this is a real network call to the
    # provider and shouldn't hold a DB connection/lock open while it runs.
    send_document(settings, provider=provider, external_document_id=external_document_id)

    with db_conn() as conn:
        conn.execute(
            text(
                f"UPDATE contracts SET status = 'sent', sent_at = {_TIMESTAMP_SQL}, "
                f"updated_at = {_TIMESTAMP_SQL} WHERE id = :id"
            ),
            {"id": contract_id},
        )


def reject_contract(contract_id: int, reason: str) -> None:
    """Reject a pending contract. Never calls a provider send method — this
    path is a pure status/DB update.
    """
    with db_conn() as conn:
        contract = _get_contract(conn, contract_id)
        if contract["status"] != "pending_approval":
            raise ContractStateError(
                f"Contract {contract_id} is '{contract['status']}', not 'pending_approval'"
            )
        conn.execute(
            text(
                f"UPDATE contracts SET status = 'rejected', rejection_reason = :reason, "
                f"updated_at = {_TIMESTAMP_SQL} WHERE id = :id"
            ),
            {"id": contract_id, "reason": reason or ""},
        )


def mark_signed_by_external_id(external_document_id: str) -> None:
    """Used by the webhook handler (and the safety-net poll job): find the
    contract by its provider document/envelope id and mark it signed.
    Silently no-ops (with a log line) if no matching contract is found,
    since webhook retries/unknown ids shouldn't raise 500s back at the
    provider.
    """
    with db_conn() as conn:
        result = conn.execute(
            text(
                f"UPDATE contracts SET status = 'signed', signed_at = {_TIMESTAMP_SQL}, "
                f"updated_at = {_TIMESTAMP_SQL} "
                "WHERE external_document_id = :external_document_id AND status != 'signed'"
            ),
            {"external_document_id": external_document_id},
        )
        if result.rowcount == 0:
            logger.info(
                "mark_signed_by_external_id: no pending contract found for external_document_id=%s",
                external_document_id,
            )


def poll_sent_contracts_for_signature() -> None:
    """Safety-net sweep: for every contract still in 'sent', ask the
    provider if it's now signed, and mark it if so. Called from jobs.py.
    """
    settings = get_settings()
    with db_conn() as conn:
        rows = conn.execute(
            text("SELECT id, provider, external_document_id FROM contracts WHERE status = 'sent'")
        ).mappings().all()

    for row in rows:
        if not row["external_document_id"]:
            continue
        try:
            signed = fetch_status_and_check_signed(
                settings, provider=row["provider"], external_document_id=row["external_document_id"]
            )
        except Exception:  # noqa: BLE001 - one bad lookup must not kill the sweep
            logger.exception(
                "poll_sent_contracts_for_signature: status check failed for contract id=%s", row["id"]
            )
            continue
        if signed:
            mark_signed_by_external_id(row["external_document_id"])
