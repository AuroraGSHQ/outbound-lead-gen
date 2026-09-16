"""Shared test fixtures for the dialer test suite.

We don't touch the real app database — each test that needs SQL gets its own
throwaway in-memory SQLite engine with just the `contacts` and `call_logs`
tables (mirroring schema.sql), and monkeypatches modules.dialer.service's
`db_conn` to yield connections from it.
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text

CONTACTS_TABLE_SQL = """
CREATE TABLE contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER,
    name TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    company_name TEXT NOT NULL DEFAULT '',
    segment TEXT NOT NULL DEFAULT 'prospect',
    service_line TEXT NOT NULL DEFAULT '',
    do_not_call INTEGER NOT NULL DEFAULT 0,
    last_contacted_at TEXT,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""

CALL_LOGS_TABLE_SQL = """
CREATE TABLE call_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER,
    call_type TEXT NOT NULL,
    direction TEXT NOT NULL DEFAULT 'outbound',
    external_call_id TEXT NOT NULL DEFAULT '',
    transcript TEXT NOT NULL DEFAULT '',
    recording_url TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    agreed_items TEXT NOT NULL DEFAULT '[]',
    deadline_mentioned TEXT,
    processed_at TEXT,
    started_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""


@pytest.fixture()
def fake_engine():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(CONTACTS_TABLE_SQL))
        conn.execute(text(CALL_LOGS_TABLE_SQL))
    yield engine
    engine.dispose()


@pytest.fixture()
def fake_db_conn(fake_engine):
    @contextmanager
    def _db_conn():
        conn = fake_engine.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    return _db_conn


@pytest.fixture()
def base_settings():
    """A plain object with just the dialer-relevant Settings fields, so tests
    don't need a real app/config.py Settings() (which needs a real DB URL /
    env). Mirrors the field names/defaults in app/config.py.
    """
    return SimpleNamespace(
        voice_agent_provider="vapi",
        vapi_api_key="test-vapi-key",
        vapi_assistant_id="asst_123",
        retell_api_key="test-retell-key",
        retell_agent_id="agent_123",
        twilio_phone_number="+15550001111",
        dialer_window_start_hour=9,
        dialer_window_end_hour=17,
        dialer_timezone="America/New_York",
        dialer_max_concurrency=3,
        dialer_max_retries=3,
        dialer_retry_spacing_minutes=120,
        dialer_cooldown_hours=24,
    )
