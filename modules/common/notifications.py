"""The one helper every other module should call to raise a notification —
per the JARVIS README: "every module above only needs to write a
notifications row, not know how it gets delivered." Delivery (SMS today)
lives entirely in modules/notifications; this just does the INSERT.
"""
from __future__ import annotations

import json
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.engine import Connection

from modules.common.db import db_conn

NotificationType = Literal[
    "payment_received",
    "deadline_due",
    "contract_needs_approval",
    "call_summary_ready",
    "missed_message",
]


def create_notification(
    type: NotificationType,
    title: str,
    body: str,
    *,
    payload: dict[str, Any] | None = None,
    contact_id: int | None = None,
    conn: Connection | None = None,
) -> int:
    """Insert a notifications row. Pass `conn` to participate in a caller's
    existing transaction (e.g. alongside the row that triggered this); omit
    it to just open and commit a short-lived one.
    """
    params = {
        "type": type,
        "title": title,
        "body": body,
        "payload": json.dumps(payload or {}),
        "contact_id": contact_id,
    }
    stmt = text(
        """
        INSERT INTO notifications (type, title, body, payload, contact_id)
        VALUES (:type, :title, :body, :payload, :contact_id)
        """
    )
    if conn is not None:
        result = conn.execute(stmt, params)
        return result.lastrowid

    with db_conn() as owned_conn:
        result = owned_conn.execute(stmt, params)
        return result.lastrowid
