"""Shared test fixtures for modules/notifications.

Points modules.common.db's `engine` at a private in-memory SQLite database
(schema.sql applied) for the duration of each test, so these tests never
touch the real data/leadgen.db and never make a live Twilio/Stripe call.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import modules.common.db as common_db

SCHEMA_SQL = (Path(__file__).resolve().parents[3] / "schema.sql").read_text()


@pytest.fixture
def notifications_db(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    raw_conn = engine.raw_connection()
    try:
        raw_conn.executescript(SCHEMA_SQL)
        raw_conn.commit()
    finally:
        raw_conn.close()

    # modules/common/db.py binds `engine` into its own namespace at import
    # time (`from app.db import engine`), so it's that name we need to swap,
    # not app.db.engine.
    monkeypatch.setattr(common_db, "engine", engine)
    yield engine
    engine.dispose()
