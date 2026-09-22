from unittest.mock import patch

from app.models import Client, CrmSyncLog, CrmSyncStatus
from app.services import crm_sync


def _client(**overrides) -> Client:
    defaults = dict(company_name="Acme GC", crm_provider="none")
    defaults.update(overrides)
    return Client(**defaults)


def test_push_to_crm_with_no_provider_logs_error(db_session):
    client = _client()
    db_session.add(client)
    db_session.commit()

    log = crm_sync.push_to_crm(db_session, client.id, {"full_name": "Jane Doe"})

    assert log.status == CrmSyncStatus.ERROR.value
    assert "no CRM connection" in log.message
    assert log.contact_summary == "Jane Doe"


def test_push_to_crm_dispatches_to_hubspot(db_session):
    client = _client(crm_provider="hubspot", crm_api_key="key-123")
    db_session.add(client)
    db_session.commit()

    with patch("app.services.crm_sync.crm_hubspot.upsert_contact") as mock_upsert:
        log = crm_sync.push_to_crm(db_session, client.id, {"email": "jane@example.com"})

    mock_upsert.assert_called_once_with("key-123", {"email": "jane@example.com"})
    assert log.status == CrmSyncStatus.SUCCESS.value


def test_push_to_crm_dispatches_to_monday_with_board_and_column_map(db_session):
    client = _client(
        crm_provider="monday",
        crm_api_key="key-456",
        crm_config={"board_id": "999", "column_map": {"email": "email_col"}},
    )
    db_session.add(client)
    db_session.commit()

    with patch("app.services.crm_sync.crm_monday.create_item") as mock_create:
        crm_sync.push_to_crm(db_session, client.id, {"email": "jane@example.com"})

    mock_create.assert_called_once_with("key-456", "999", {"email": "email_col"}, {"email": "jane@example.com"})


def test_push_to_crm_dispatches_to_highlevel_with_location(db_session):
    client = _client(crm_provider="highlevel", crm_api_key="key-789", crm_config={"location_id": "loc-1"})
    db_session.add(client)
    db_session.commit()

    with patch("app.services.crm_sync.crm_highlevel.upsert_contact") as mock_upsert:
        crm_sync.push_to_crm(db_session, client.id, {"phone": "555-1212"})

    mock_upsert.assert_called_once_with("key-789", "loc-1", {"phone": "555-1212"})


def test_push_to_crm_records_error_without_raising(db_session):
    client = _client(crm_provider="hubspot", crm_api_key="key-123")
    db_session.add(client)
    db_session.commit()

    with patch("app.services.crm_sync.crm_hubspot.upsert_contact", side_effect=RuntimeError("boom")):
        log = crm_sync.push_to_crm(db_session, client.id, {"email": "jane@example.com"})

    assert log.status == CrmSyncStatus.ERROR.value
    assert "boom" in log.message


def test_list_logs_filters_by_client(db_session):
    client_a = _client(company_name="A")
    client_b = _client(company_name="B")
    db_session.add_all([client_a, client_b])
    db_session.commit()
    db_session.add_all(
        [
            CrmSyncLog(client_id=client_a.id, provider="hubspot", status="success", contact_summary="x"),
            CrmSyncLog(client_id=client_b.id, provider="hubspot", status="success", contact_summary="y"),
        ]
    )
    db_session.commit()

    logs = crm_sync.list_logs(db_session, client_id=client_a.id)

    assert len(logs) == 1
    assert logs[0].client_id == client_a.id


def test_parse_lead_details_delegates_to_claude_drafter():
    settings = crm_sync.Settings()
    fake_result = {"full_name": "Jane Doe", "email": "jane@example.com"}
    with patch("app.services.crm_sync.ClaudeDrafter") as mock_drafter_cls:
        mock_drafter_cls.return_value.parse_lead_details.return_value = fake_result
        result = crm_sync.parse_lead_details(settings, "some pasted text")

    mock_drafter_cls.return_value.parse_lead_details.assert_called_once_with("some pasted text")
    assert result == fake_result
