"""Test setup: point the app at a throwaway sqlite DB (not the real
data/leadgen.db) before anything imports app.db, and make sure schema.sql
has been applied to it.
"""
from __future__ import annotations

import os
import tempfile

import pytest

_tmpdir = tempfile.mkdtemp(prefix="contracts_tests_")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmpdir}/test.db"
os.environ.setdefault("CONTRACT_TEMPLATE_ID", "tmpl_test_123")
os.environ.setdefault("OWNER_UI_USERNAME", "owner")
os.environ.setdefault("OWNER_UI_PASSWORD", "secret")
os.environ.setdefault("PANDADOC_API_KEY", "test-pandadoc-key")

from app.db import init_db  # noqa: E402
from sqlalchemy import text  # noqa: E402

from modules.common.db import db_conn  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _init_test_db():
    init_db()


@pytest.fixture(autouse=True)
def _clean_tables():
    """Each test starts with empty contracts/contacts/notifications tables."""
    with db_conn() as conn:
        conn.execute(text("DELETE FROM contracts"))
        conn.execute(text("DELETE FROM notifications"))
        conn.execute(text("DELETE FROM contacts"))
    yield


def make_contact(name="Jane Client", email="jane@example.com", phone="555-1234", company="Acme LLC") -> int:
    with db_conn() as conn:
        result = conn.execute(
            text(
                "INSERT INTO contacts (name, email, phone, company_name) "
                "VALUES (:name, :email, :phone, :company)"
            ),
            {"name": name, "email": email, "phone": phone, "company": company},
        )
        return result.lastrowid
