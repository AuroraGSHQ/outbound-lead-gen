"""Unit tests for modules/contracts/service.py.

No live network calls — every provider client call is mocked out. Focus:
- reject_contract must never call the send API
- approve_contract / create_contract_for_closed_deal build the right
  filled_data and drive the right status transitions
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from sqlalchemy import text

from modules.common.db import db_conn
from modules.contracts import service
from modules.contracts.tests.conftest import make_contact


def _insert_contract(**overrides) -> int:
    defaults = {
        "contact_id": None,
        "call_log_id": None,
        "template_id": "tmpl_test_123",
        "provider": "pandadoc",
        "external_document_id": "doc_existing",
        "filled_data": "{}",
        "status": "pending_approval",
    }
    defaults.update(overrides)
    with db_conn() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO contracts
                    (contact_id, call_log_id, template_id, provider, external_document_id,
                     filled_data, status)
                VALUES
                    (:contact_id, :call_log_id, :template_id, :provider, :external_document_id,
                     :filled_data, :status)
                """
            ),
            defaults,
        )
        return result.lastrowid


def _get_contract(contract_id: int) -> dict:
    with db_conn() as conn:
        row = conn.execute(
            text("SELECT * FROM contracts WHERE id = :id"), {"id": contract_id}
        ).mappings().first()
        return dict(row)


# ----------------------------------------------------------------------
# create_contract_for_closed_deal
# ----------------------------------------------------------------------
def test_create_contract_for_closed_deal_builds_filled_data_and_notifies():
    contact_id = make_contact(name="Jane Client", email="jane@example.com", company="Acme LLC")

    with patch.object(service, "create_document", return_value="doc_abc123") as mock_create:
        contract_id = service.create_contract_for_closed_deal(
            contact_id,
            None,
            {"price": "5000", "scope": "Website redesign", "deadline": "2026-01-01"},
        )

    assert isinstance(contract_id, int)
    mock_create.assert_called_once()
    _, kwargs = mock_create.call_args
    assert kwargs["recipient_email"] == "jane@example.com"
    assert kwargs["recipient_name"] == "Jane Client"
    assert kwargs["filled_data"]["price"] == "5000"
    assert kwargs["filled_data"]["scope"] == "Website redesign"
    assert kwargs["filled_data"]["company_name"] == "Acme LLC"

    row = _get_contract(contract_id)
    assert row["status"] == "pending_approval"
    assert row["provider"] == "pandadoc"
    assert row["external_document_id"] == "doc_abc123"
    filled = json.loads(row["filled_data"])
    assert filled["price"] == "5000"
    assert filled["contact_email"] == "jane@example.com"

    with db_conn() as conn:
        notif = conn.execute(
            text("SELECT * FROM notifications WHERE type = 'contract_needs_approval'")
        ).mappings().first()
    assert notif is not None
    assert notif["contact_id"] == contact_id
    payload = json.loads(notif["payload"])
    assert payload["contract_id"] == contract_id


def test_create_contract_for_closed_deal_unknown_contact_raises():
    with pytest.raises(service.ContractNotFoundError):
        service.create_contract_for_closed_deal(999999, None, {"price": "100"})


# ----------------------------------------------------------------------
# reject_contract
# ----------------------------------------------------------------------
def test_reject_contract_never_calls_send():
    contact_id = make_contact()
    contract_id = _insert_contract(contact_id=contact_id)

    with patch.object(service, "send_document") as mock_send:
        service.reject_contract(contract_id, "client backed out")

    mock_send.assert_not_called()
    row = _get_contract(contract_id)
    assert row["status"] == "rejected"
    assert row["rejection_reason"] == "client backed out"


def test_reject_contract_requires_pending_approval():
    contract_id = _insert_contract(status="approved")
    with patch.object(service, "send_document") as mock_send:
        with pytest.raises(service.ContractStateError):
            service.reject_contract(contract_id, "too late")
    mock_send.assert_not_called()
    # status untouched
    assert _get_contract(contract_id)["status"] == "approved"


# ----------------------------------------------------------------------
# approve_contract
# ----------------------------------------------------------------------
def test_approve_contract_sends_and_transitions_to_sent():
    contract_id = _insert_contract(external_document_id="doc_xyz", provider="pandadoc")

    with patch.object(service, "send_document") as mock_send:
        service.approve_contract(contract_id)

    mock_send.assert_called_once()
    _, kwargs = mock_send.call_args
    assert kwargs["provider"] == "pandadoc"
    assert kwargs["external_document_id"] == "doc_xyz"

    row = _get_contract(contract_id)
    assert row["status"] == "sent"
    assert row["sent_at"] is not None


def test_approve_contract_requires_pending_approval():
    contract_id = _insert_contract(status="sent")
    with patch.object(service, "send_document") as mock_send:
        with pytest.raises(service.ContractStateError):
            service.approve_contract(contract_id)
    mock_send.assert_not_called()


def test_approve_contract_unknown_id_raises():
    with pytest.raises(service.ContractNotFoundError):
        service.approve_contract(999999)


# ----------------------------------------------------------------------
# mark_signed_by_external_id
# ----------------------------------------------------------------------
def test_mark_signed_by_external_id():
    contract_id = _insert_contract(status="sent", external_document_id="doc_signed_1")

    service.mark_signed_by_external_id("doc_signed_1")

    row = _get_contract(contract_id)
    assert row["status"] == "signed"
    assert row["signed_at"] is not None


def test_mark_signed_by_external_id_unknown_is_noop():
    # Should not raise even if nothing matches.
    service.mark_signed_by_external_id("doc_does_not_exist")


# ----------------------------------------------------------------------
# provider dispatch
# ----------------------------------------------------------------------
def test_create_document_unknown_provider_raises():
    class FakeSettings:
        contracts_provider = "carrier_pigeon"

    with pytest.raises(ValueError):
        service.create_document(
            FakeSettings(),
            template_id="t",
            filled_data={},
            recipient_email="a@b.com",
            recipient_name="A",
        )
