"""Shared raw-SQL access to the schema.sql tables (contacts, call_logs,
deadlines, contracts, notifications).

These tables aren't mapped as SQLAlchemy ORM models (see app/models.py for
the leads/conversations/messages/meetings ones) — they're applied straight
from schema.sql by app/db.py::init_db(). Modules under modules/ talk to them
with plain SQL through the same engine/connection pool the rest of the app
uses, via `db_conn()` below, instead of each reinventing a connection.

Usage:
    from modules.common.db import db_conn
    from sqlalchemy import text

    with db_conn() as conn:
        conn.execute(text("UPDATE contacts SET do_not_call = 1 WHERE id = :id"), {"id": contact_id})
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Connection

from app.db import engine


@contextmanager
def db_conn() -> Iterator[Connection]:
    conn = engine.connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
