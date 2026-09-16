"""Tests for modules.sync.service.sync_emails: dedup, flagging, needs_reply,
and the once-per-row notification firing — all against a fake in-memory DB
and a mocked Gmail client (no live network, no real DB).
"""
from __future__ import annotations

from sqlalchemy import text

import modules.sync.service as service
from modules.sync.gmail_client import GmailMessageSummary


def _insert_contact(conn, **overrides):
    defaults = dict(name="Known Client", email="client@known.com", company_name="Known Co")
    defaults.update(overrides)
    conn.execute(
        text("INSERT INTO contacts (name, email, company_name) VALUES (:name, :email, :company_name)"),
        defaults,
    )


def _patch_common(monkeypatch, fake_db_conn, base_settings, messages, *, needs_reply=True):
    monkeypatch.setattr(service, "db_conn", fake_db_conn)
    monkeypatch.setattr(service, "get_settings", lambda: base_settings)
    monkeypatch.setattr(service.gmail_client, "build_service", lambda cid, secret, token: object())
    monkeypatch.setattr(service.gmail_client, "get_own_address", lambda svc: "biz@ourco.com")
    monkeypatch.setattr(service.gmail_client, "list_recent_messages", lambda svc, since_ts, **kw: messages)
    monkeypatch.setattr(service, "classify_needs_reply", lambda subject, snippet: needs_reply)


def test_sync_emails_skips_inbox_with_no_refresh_token(monkeypatch, fake_db_conn, base_settings):
    base_settings.gmail_personal_refresh_token = ""
    _patch_common(monkeypatch, fake_db_conn, base_settings, [])

    result = service.sync_emails()

    assert result["business_seen"] == 0
    assert result["personal_seen"] == 0


def test_sync_emails_inbound_unmatched_flags_and_notifies(monkeypatch, fake_db_conn, base_settings):
    base_settings.gmail_personal_refresh_token = ""  # only exercise business inbox
    msg = GmailMessageSummary(
        message_id="msg-1",
        thread_id="thread-1",
        from_address="Jane Prospect <jane@newlead.com>",
        to_address="biz@ourco.com",
        subject="Question about your services",
        snippet="Hi, do you do junk removal in Austin?",
        internal_date="1700000000000",
    )
    _patch_common(monkeypatch, fake_db_conn, base_settings, [msg], needs_reply=True)

    result = service.sync_emails()

    assert result["business_seen"] == 1
    assert result["new_rows"] == 1
    assert result["flagged"] == 1
    assert result["needs_reply"] == 1
    assert result["notifications_fired"] == 1

    with fake_db_conn() as conn:
        row = conn.execute(text("SELECT * FROM emails WHERE gmail_message_id = 'msg-1'")).mappings().first()
        assert row["direction"] == "inbound"
        assert row["contact_id"] is None
        assert row["flagged"] == 1
        assert row["needs_reply"] == 1
        assert row["notified"] == 1

        notif = conn.execute(text("SELECT * FROM notifications")).mappings().first()
        assert notif["type"] == "missed_message"


def test_sync_emails_outbound_matched_contact_no_flag_no_classification(
    monkeypatch, fake_db_conn, base_settings
):
    base_settings.gmail_personal_refresh_token = ""
    msg = GmailMessageSummary(
        message_id="msg-2",
        thread_id="thread-2",
        from_address="biz@ourco.com",
        to_address="Known Client <client@known.com>",
        subject="Following up",
        snippet="Just checking in on the estimate.",
        internal_date="1700000001000",
    )

    classify_calls = []
    _patch_common(monkeypatch, fake_db_conn, base_settings, [msg], needs_reply=True)
    monkeypatch.setattr(
        service,
        "classify_needs_reply",
        lambda subject, snippet: classify_calls.append((subject, snippet)) or True,
    )

    with fake_db_conn() as conn:
        _insert_contact(conn)

    result = service.sync_emails()

    assert result["new_rows"] == 1
    assert result["flagged"] == 0
    assert result["needs_reply"] == 0  # outbound messages are never classified
    assert result["notifications_fired"] == 0
    assert classify_calls == []  # classify_needs_reply must be skipped for outbound

    with fake_db_conn() as conn:
        row = conn.execute(text("SELECT * FROM emails WHERE gmail_message_id = 'msg-2'")).mappings().first()
        assert row["direction"] == "outbound"
        assert row["contact_id"] == 1
        assert row["flagged"] == 0
        assert row["notified"] == 0


def test_sync_emails_dedup_on_second_run(monkeypatch, fake_db_conn, base_settings):
    base_settings.gmail_personal_refresh_token = ""
    msg = GmailMessageSummary(
        message_id="msg-3",
        thread_id="thread-3",
        from_address="jane@newlead.com",
        to_address="biz@ourco.com",
        subject="Hello",
        snippet="hi",
        internal_date="1700000002000",
    )
    _patch_common(monkeypatch, fake_db_conn, base_settings, [msg], needs_reply=False)

    first = service.sync_emails()
    second = service.sync_emails()

    assert first["new_rows"] == 1
    assert second["new_rows"] == 0  # already ingested by (inbox, gmail_message_id)

    with fake_db_conn() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM emails WHERE gmail_message_id = 'msg-3'")).scalar()
        assert count == 1


def test_sync_emails_flagged_but_not_needs_reply_still_notifies_once(
    monkeypatch, fake_db_conn, base_settings
):
    base_settings.gmail_personal_refresh_token = ""
    msg = GmailMessageSummary(
        message_id="msg-4",
        thread_id="thread-4",
        from_address="stranger@nowhere.com",
        to_address="biz@ourco.com",
        subject="fyi",
        snippet="automated notice",
        internal_date="1700000003000",
    )
    _patch_common(monkeypatch, fake_db_conn, base_settings, [msg], needs_reply=False)

    result = service.sync_emails()

    assert result["flagged"] == 1
    assert result["needs_reply"] == 0
    assert result["notifications_fired"] == 1  # flagged alone is enough to notify
