"""End-to-end (but network-free) tests for run_dial_window: DB is a fake
in-memory SQLite engine, the voice agent client is mocked, and the clock is
patched so window gating is deterministic.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

from sqlalchemy import text

import modules.dialer.service as service


def _insert_contact(conn, **overrides):
    defaults = dict(
        id=1,
        name="Jane",
        phone="+15551234567",
        segment="prospect",
        service_line="marketing",
        do_not_call=0,
        last_contacted_at=None,
    )
    defaults.update(overrides)
    conn.execute(
        text(
            """
            INSERT INTO contacts (id, name, phone, segment, service_line, do_not_call, last_contacted_at)
            VALUES (:id, :name, :phone, :segment, :service_line, :do_not_call, :last_contacted_at)
            """
        ),
        defaults,
    )


def test_run_dial_window_noop_outside_window(monkeypatch, fake_db_conn, base_settings):
    monkeypatch.setattr(service, "db_conn", fake_db_conn)
    monkeypatch.setattr(service, "_local_now", lambda settings: datetime(2026, 6, 15, 20, 0))

    result = service.run_dial_window(base_settings)

    assert result["ran"] is False
    assert result["reason"] == "outside_dial_window"


def test_run_dial_window_places_call_and_logs_it(monkeypatch, fake_db_conn, fake_engine, base_settings):
    with fake_db_conn() as conn:
        _insert_contact(conn)

    monkeypatch.setattr(service, "db_conn", fake_db_conn)
    monkeypatch.setattr(service, "_local_now", lambda settings: datetime(2026, 6, 15, 12, 0))
    monkeypatch.setattr(service, "_now_utc", lambda: datetime(2026, 6, 15, 16, 0))

    fake_client = MagicMock()
    fake_client.place_call.return_value = {"external_call_id": "call_abc123", "status": "queued"}
    monkeypatch.setattr(service, "VoiceAgentClient", lambda settings: fake_client)

    result = service.run_dial_window(base_settings)

    assert result["ran"] is True
    assert result["placed"] == 1
    assert result["by_segment"]["prospect"] == 1
    fake_client.place_call.assert_called_once()
    _, kwargs = fake_client.place_call.call_args
    assert kwargs["to_number"] == "+15551234567"
    assert kwargs["context"]["segment"] == "prospect"

    with fake_engine.connect() as conn:
        call_row = conn.execute(text("SELECT external_call_id, outcome, contact_id FROM call_logs")).fetchone()
        assert call_row.external_call_id == "call_abc123"
        assert call_row.outcome == "queued"
        assert call_row.contact_id == 1

        contact_row = conn.execute(text("SELECT last_contacted_at FROM contacts WHERE id = 1")).fetchone()
        assert contact_row.last_contacted_at is not None


def test_run_dial_window_skips_contact_flagged_do_not_call_mid_run(
    monkeypatch, fake_db_conn, fake_engine, base_settings
):
    with fake_db_conn() as conn:
        _insert_contact(conn, do_not_call=0)

    monkeypatch.setattr(service, "db_conn", fake_db_conn)
    monkeypatch.setattr(service, "_local_now", lambda settings: datetime(2026, 6, 15, 12, 0))
    monkeypatch.setattr(service, "_now_utc", lambda: datetime(2026, 6, 15, 16, 0))

    # Simulate the compliance-critical race: do_not_call flips true AFTER the
    # initial eligibility query (step b) but BEFORE the per-contact dial
    # (step d). We patch _fetch_eligible_contacts to return the stale batch,
    # then flip the flag in the DB directly, so only the immediate re-check
    # in run_dial_window can catch it.
    stale_contact = {
        "id": 1,
        "lead_id": None,
        "name": "Jane",
        "phone": "+15551234567",
        "email": "",
        "company_name": "",
        "segment": "prospect",
        "service_line": "marketing",
        "do_not_call": 0,
        "last_contacted_at": None,
        "notes": "",
    }
    monkeypatch.setattr(service, "_fetch_eligible_contacts", lambda conn, settings, now: [stale_contact])

    with fake_db_conn() as conn:
        conn.execute(text("UPDATE contacts SET do_not_call = 1 WHERE id = 1"))

    fake_client = MagicMock()
    monkeypatch.setattr(service, "VoiceAgentClient", lambda settings: fake_client)

    result = service.run_dial_window(base_settings)

    assert result["placed"] == 0
    assert result["skipped_do_not_call"] == 1
    fake_client.place_call.assert_not_called()

    with fake_engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM call_logs")).scalar()
        assert count == 0
