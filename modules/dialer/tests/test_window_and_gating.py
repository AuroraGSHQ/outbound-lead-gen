from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import text

from modules.dialer.service import (
    _refetch_do_not_call,
    in_dial_window,
    retry_gate,
)

TZ = ZoneInfo("America/New_York")


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, 15, hour, minute, tzinfo=TZ)


# --- in_dial_window -----------------------------------------------------


def test_in_window_at_start_hour_inclusive(base_settings):
    assert in_dial_window(base_settings, _at(9, 0)) is True


def test_in_window_mid_day(base_settings):
    assert in_dial_window(base_settings, _at(13, 30)) is True


def test_out_of_window_before_start(base_settings):
    assert in_dial_window(base_settings, _at(8, 59)) is False


def test_out_of_window_at_end_hour_exclusive(base_settings):
    assert in_dial_window(base_settings, _at(17, 0)) is False


def test_out_of_window_late_night(base_settings):
    assert in_dial_window(base_settings, _at(23, 0)) is False


# --- retry_gate -----------------------------------------------------------


def _insert_contact(conn, **overrides):
    defaults = dict(id=1, name="C", phone="+15551234567", segment="prospect", do_not_call=0)
    defaults.update(overrides)
    conn.execute(
        text(
            "INSERT INTO contacts (id, name, phone, segment, do_not_call) "
            "VALUES (:id, :name, :phone, :segment, :do_not_call)"
        ),
        defaults,
    )


def _insert_call(conn, contact_id, outcome, created_at, started_at=None):
    conn.execute(
        text(
            "INSERT INTO call_logs (contact_id, call_type, outcome, created_at, started_at) "
            "VALUES (:contact_id, 'ai_outbound', :outcome, :created_at, :started_at)"
        ),
        {
            "contact_id": contact_id,
            "outcome": outcome,
            "created_at": created_at,
            "started_at": started_at or created_at,
        },
    )


def test_retry_gate_allows_contact_with_no_history(fake_db_conn, base_settings):
    with fake_db_conn() as conn:
        _insert_contact(conn)
        allowed, reason = retry_gate(conn, base_settings, 1, datetime(2026, 6, 15, 12, 0))
    assert allowed is True


def test_retry_gate_allows_when_under_max_retries_and_spacing_elapsed(fake_db_conn, base_settings):
    now = datetime(2026, 6, 15, 12, 0)
    with fake_db_conn() as conn:
        _insert_contact(conn)
        # One no-answer attempt, 3 hours ago (spacing is 120 min) — should be allowed.
        _insert_call(conn, 1, "no_answer", (now - timedelta(hours=3)).isoformat())
        allowed, reason = retry_gate(conn, base_settings, 1, now)
    assert allowed is True


def test_retry_gate_blocks_when_spacing_not_elapsed(fake_db_conn, base_settings):
    now = datetime(2026, 6, 15, 12, 0)
    with fake_db_conn() as conn:
        _insert_contact(conn)
        # One no-answer attempt only 10 minutes ago — spacing is 120 min, should block.
        _insert_call(conn, 1, "no_answer", (now - timedelta(minutes=10)).isoformat())
        allowed, reason = retry_gate(conn, base_settings, 1, now)
    assert allowed is False
    assert reason == "retry_spacing_not_elapsed"


def test_retry_gate_blocks_when_max_retries_reached(fake_db_conn, base_settings):
    now = datetime(2026, 6, 15, 12, 0)
    with fake_db_conn() as conn:
        _insert_contact(conn)
        # 3 no-answer attempts (== dialer_max_retries), each spaced well apart.
        for i in range(3):
            _insert_call(conn, 1, "busy", (now - timedelta(hours=10 + i)).isoformat())
        allowed, reason = retry_gate(conn, base_settings, 1, now)
    assert allowed is False
    assert "max_retries_reached" in reason


def test_retry_gate_resets_after_real_contact(fake_db_conn, base_settings):
    now = datetime(2026, 6, 15, 12, 0)
    with fake_db_conn() as conn:
        _insert_contact(conn)
        # Old no-answer streak, then a real contact more recently -> should reset counting.
        _insert_call(conn, 1, "no_answer", (now - timedelta(days=10)).isoformat())
        _insert_call(conn, 1, "no_answer", (now - timedelta(days=9)).isoformat())
        _insert_call(conn, 1, "no_answer", (now - timedelta(days=8)).isoformat())
        _insert_call(conn, 1, "completed", (now - timedelta(days=5)).isoformat())
        allowed, reason = retry_gate(conn, base_settings, 1, now)
    assert allowed is True


# --- do_not_call re-check --------------------------------------------------


def test_refetch_do_not_call_reflects_latest_value(fake_db_conn):
    with fake_db_conn() as conn:
        _insert_contact(conn, do_not_call=0)
        assert _refetch_do_not_call(conn, 1) is False

    # Simulate the flag flipping mid-run, in a separate connection/transaction.
    with fake_db_conn() as conn:
        conn.execute(text("UPDATE contacts SET do_not_call = 1 WHERE id = 1"))

    with fake_db_conn() as conn:
        assert _refetch_do_not_call(conn, 1) is True


def test_refetch_do_not_call_true_for_missing_contact(fake_db_conn):
    with fake_db_conn() as conn:
        assert _refetch_do_not_call(conn, 999) is True
