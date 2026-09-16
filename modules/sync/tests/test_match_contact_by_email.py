"""Unit tests for modules.sync.service.match_contact_by_email."""
from __future__ import annotations

from sqlalchemy import text

from modules.sync.service import match_contact_by_email


def _insert_contact(conn, **overrides):
    defaults = dict(name="Jane", email="jane@example.com", company_name="Acme")
    defaults.update(overrides)
    conn.execute(
        text("INSERT INTO contacts (name, email, company_name) VALUES (:name, :email, :company_name)"),
        defaults,
    )


def test_match_contact_by_email_exact_match(fake_db_conn):
    with fake_db_conn() as conn:
        _insert_contact(conn)
        contact_id = match_contact_by_email(conn, "jane@example.com")
    assert contact_id == 1


def test_match_contact_by_email_case_insensitive(fake_db_conn):
    with fake_db_conn() as conn:
        _insert_contact(conn, email="Jane@Example.COM")
        contact_id = match_contact_by_email(conn, "jane@example.com")
    assert contact_id == 1


def test_match_contact_by_email_no_match(fake_db_conn):
    with fake_db_conn() as conn:
        _insert_contact(conn)
        contact_id = match_contact_by_email(conn, "nobody@nowhere.com")
    assert contact_id is None


def test_match_contact_by_email_blank_address_never_matches_blank_contacts(fake_db_conn):
    with fake_db_conn() as conn:
        _insert_contact(conn, email="")  # contact with no email on file
        contact_id = match_contact_by_email(conn, "")
    assert contact_id is None


def test_match_contact_by_email_whitespace_and_none_safe(fake_db_conn):
    with fake_db_conn() as conn:
        _insert_contact(conn)
        assert match_contact_by_email(conn, "   ") is None
