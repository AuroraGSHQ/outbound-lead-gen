"""Delivery-sweep success/failure/give-up logic (modules/notifications/service.py).

No live network calls: twilio_sms.send_sms is monkeypatched throughout.
"""
from __future__ import annotations

from sqlalchemy import text

from modules.common.db import db_conn
from modules.notifications import service, twilio_sms


def _insert_notification(delivery_error: str = "") -> int:
    with db_conn() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO notifications (type, title, body, delivery_error)
                VALUES ('payment_received', 'Payment received', 'Body text', :delivery_error)
                """
            ),
            {"delivery_error": delivery_error},
        )
        return result.lastrowid


def _fetch(notification_id: int) -> dict:
    with db_conn() as conn:
        row = conn.execute(
            text("SELECT * FROM notifications WHERE id = :id"), {"id": notification_id}
        ).mappings().first()
        return dict(row)


def test_deliver_pending_success(notifications_db, monkeypatch):
    monkeypatch.setattr(twilio_sms, "send_sms", lambda settings, title, body: "SMxxxx")

    nid = _insert_notification()
    counts = service.deliver_pending_notifications()

    row = _fetch(nid)
    assert row["delivered"] == 1
    assert row["delivered_at"]
    assert row["delivery_error"] == ""
    assert counts == {"delivered": 1, "failed": 0, "skipped": 0}


def test_deliver_pending_failure_is_retried_next_sweep(notifications_db, monkeypatch):
    def _boom(settings, title, body):
        raise RuntimeError("Twilio: invalid 'To' phone number")

    monkeypatch.setattr(twilio_sms, "send_sms", _boom)

    nid = _insert_notification()
    counts = service.deliver_pending_notifications()

    row = _fetch(nid)
    assert row["delivered"] == 0
    assert row["delivered_at"] is None
    assert row["delivery_error"].startswith("[attempt 1]")
    assert "invalid 'To' phone number" in row["delivery_error"]
    assert counts == {"delivered": 0, "failed": 1, "skipped": 0}

    # Still delivered=0, so it's picked up again next sweep, and the
    # attempt counter increments.
    counts_2 = service.deliver_pending_notifications()
    row_2 = _fetch(nid)
    assert row_2["delivery_error"].startswith("[attempt 2]")
    assert counts_2 == {"delivered": 0, "failed": 1, "skipped": 0}


def test_deliver_pending_gives_up_after_max_attempts(notifications_db, monkeypatch):
    calls = []

    def _boom(settings, title, body):
        calls.append(1)
        raise RuntimeError("permanently broken")

    monkeypatch.setattr(twilio_sms, "send_sms", _boom)

    # Pre-seed a row already at MAX_DELIVERY_ATTEMPTS via the same
    # "[attempt N] ..." prefix the sweep itself writes.
    already_exhausted_error = f"[attempt {service.MAX_DELIVERY_ATTEMPTS}] permanently broken"
    nid = _insert_notification(delivery_error=already_exhausted_error)

    counts = service.deliver_pending_notifications()

    row = _fetch(nid)
    assert row["delivered"] == 0
    assert row["delivery_error"] == already_exhausted_error  # untouched — never re-attempted
    assert calls == []  # send_sms must not have been called at all
    assert counts == {"delivered": 0, "failed": 0, "skipped": 1}


def test_manual_retry_bypasses_give_up_gate(notifications_db, monkeypatch):
    monkeypatch.setattr(twilio_sms, "send_sms", lambda settings, title, body: "SMxxxx")

    already_exhausted_error = f"[attempt {service.MAX_DELIVERY_ATTEMPTS}] permanently broken"
    nid = _insert_notification(delivery_error=already_exhausted_error)

    # A plain sweep would skip this row entirely (see test above)...
    result = service.retry_notification(nid)

    # ...but the explicit manual retry always attempts once, regardless.
    assert result == {"id": nid, "delivered": True}
    row = _fetch(nid)
    assert row["delivered"] == 1
    assert row["delivery_error"] == ""


def test_manual_retry_unknown_id_raises(notifications_db):
    import pytest

    with pytest.raises(ValueError):
        service.retry_notification(999999)


def test_handle_stripe_payment_succeeded_writes_notification(notifications_db):
    event = {
        "type": "payment_intent.succeeded",
        "data": {
            "object": {
                "id": "pi_123",
                "amount_received": 15000,
                "currency": "usd",
                "receipt_email": "client@example.com",
            }
        },
    }
    service.handle_stripe_payment_succeeded(event)

    with db_conn() as conn:
        row = conn.execute(
            text("SELECT * FROM notifications WHERE type = 'payment_received'")
        ).mappings().first()

    assert row is not None
    assert "150.00 USD" in row["title"]
    assert "client@example.com" in row["body"]
    assert "pi_123" in row["body"]
