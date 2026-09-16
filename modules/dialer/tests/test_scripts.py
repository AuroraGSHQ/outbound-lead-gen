from __future__ import annotations

from sqlalchemy import text

from modules.dialer.service import (
    build_callback_requested_script,
    build_current_client_script,
    build_past_client_script,
    build_prospect_script,
    build_script_for_contact,
)


def test_prospect_script_marketing_only():
    contact = {"name": "Jane", "company_name": "Acme Co", "service_line": "marketing"}
    script = build_prospect_script(contact)
    assert "Jane" in script
    assert "Acme Co" in script
    assert "marketing services" in script
    assert "junk removal" not in script


def test_prospect_script_both_service_lines():
    contact = {"name": "Bob", "company_name": "Bob's Biz", "service_line": "marketing,junk_removal"}
    script = build_prospect_script(contact)
    assert "marketing services" in script
    assert "junk removal services" in script
    assert " and " in script


def test_prospect_script_handles_missing_service_line():
    contact = {"name": "No Line", "company_name": "X", "service_line": ""}
    script = build_prospect_script(contact)
    assert "our services" in script


def test_current_client_script_is_checkin_not_pitch():
    contact = {"name": "Sam", "company_name": "Sam LLC", "service_line": "junk_removal"}
    script = build_current_client_script(contact)
    assert "check-in" in script
    assert "existing/current client" in script


def test_past_client_script_mentions_reengagement():
    contact = {"name": "Old Client", "company_name": "OldCo", "service_line": "marketing"}
    script = build_past_client_script(contact)
    assert "re-engagement" in script or "reconnect" in script


def test_callback_script_uses_last_summary_when_present():
    script = build_callback_requested_script(
        {"name": "Cal", "company_name": "CallCo"}, "They wanted a quote for weekly pickup."
    )
    assert "They wanted a quote for weekly pickup." in script


def test_callback_script_falls_back_when_no_summary():
    script = build_callback_requested_script({"name": "Cal", "company_name": "CallCo"}, "")
    assert "no specific ask was recorded" in script


def test_build_script_for_contact_pulls_callback_summary_from_db(fake_db_conn):
    with fake_db_conn() as conn:
        conn.execute(
            text(
                "INSERT INTO contacts (id, name, phone, segment) "
                "VALUES (1, 'Cal', '+15551112222', 'callback_requested')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO call_logs (contact_id, call_type, outcome, summary, created_at) "
                "VALUES (1, 'ai_outbound', 'callback_requested', 'Wants pricing for Q4', '2026-01-01T00:00:00.000Z')"
            )
        )

    with fake_db_conn() as conn:
        contact = {"id": 1, "name": "Cal", "company_name": "", "segment": "callback_requested"}
        script = build_script_for_contact(conn, contact)
        assert "Wants pricing for Q4" in script


def test_build_script_for_contact_dispatches_by_segment(fake_db_conn):
    with fake_db_conn() as conn:
        prospect_script = build_script_for_contact(
            conn, {"id": 1, "segment": "prospect", "name": "A", "company_name": "", "service_line": ""}
        )
        client_script = build_script_for_contact(
            conn, {"id": 2, "segment": "current_client", "name": "B", "company_name": "", "service_line": ""}
        )
    assert "discovery" in prospect_script
    assert "check-in" in client_script
