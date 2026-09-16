"""Shared test fixtures for the sync test suite.

Same pattern as modules/dialer/tests/conftest.py: no real app database, each
test that needs SQL gets its own throwaway in-memory SQLite engine mirroring
just the schema.sql tables this module touches (contacts, emails,
notifications), and monkeypatches modules.sync.service's `db_conn` to yield
connections from it.
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

EMAILS_TABLE_SQL = """
CREATE TABLE emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER,
    inbox TEXT NOT NULL,
    direction TEXT NOT NULL,
    gmail_message_id TEXT NOT NULL DEFAULT '',
    gmail_thread_id TEXT NOT NULL DEFAULT '',
    from_address TEXT NOT NULL DEFAULT '',
    to_address TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    snippet TEXT NOT NULL DEFAULT '',
    flagged INTEGER NOT NULL DEFAULT 0,
    needs_reply INTEGER NOT NULL DEFAULT 0,
    notified INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE UNIQUE INDEX idx_emails_inbox_message_unique ON emails(inbox, gmail_message_id);
"""

NOTIFICATIONS_TABLE_SQL = """
CREATE TABLE notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL DEFAULT '{}',
    contact_id INTEGER,
    delivered INTEGER NOT NULL DEFAULT 0,
    delivered_at TEXT,
    delivery_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""


@pytest.fixture()
def fake_engine():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(CONTACTS_TABLE_SQL))
        for stmt in EMAILS_TABLE_SQL.strip().split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
        conn.execute(text(NOTIFICATIONS_TABLE_SQL))
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
    """A plain object with just the sync-relevant Settings fields, so tests
    don't need a real app/config.py Settings() (which needs a real DB URL /
    env). Mirrors the field names/defaults in app/config.py.
    """
    return SimpleNamespace(
        gmail_oauth_client_id="test-client-id",
        gmail_oauth_client_secret="test-client-secret",
        gmail_business_refresh_token="business-refresh-token",
        gmail_personal_refresh_token="personal-refresh-token",
        vibe_prospecting_api_key="test-vibe-key",
        sync_stale_prospect_days=60,
        anthropic_api_key="test-anthropic-key",
        anthropic_model="claude-sonnet-5",
    )
