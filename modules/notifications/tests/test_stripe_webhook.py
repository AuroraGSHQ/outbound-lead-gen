"""Stripe webhook signature verification path (modules/notifications/router.py).

Uses a standalone FastAPI app wrapping just this router (not app.main, which
this module must not import from / touch) with `stripe.Webhook.construct_event`
monkeypatched — no live network calls and no real Stripe signing key needed.
"""
from __future__ import annotations

import stripe
from fastapi import FastAPI
from fastapi.testclient import TestClient

from modules.notifications import router as notifications_router


def _make_client() -> TestClient:
    app = FastAPI()
    app.include_router(notifications_router.router)
    return TestClient(app)


def test_valid_signature_processes_payment_succeeded(notifications_db, monkeypatch):
    event = {
        "type": "payment_intent.succeeded",
        "data": {"object": {"id": "pi_abc", "amount_received": 5000, "currency": "usd"}},
    }
    monkeypatch.setattr(
        stripe.Webhook, "construct_event", lambda payload, sig_header, secret: event
    )

    client = _make_client()
    resp = client.post(
        "/notifications/webhooks/stripe",
        content=b'{"fake": "payload"}',
        headers={"Stripe-Signature": "t=1,v1=fake"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

    from sqlalchemy import text

    from modules.common.db import db_conn

    with db_conn() as conn:
        row = conn.execute(
            text("SELECT * FROM notifications WHERE type = 'payment_received'")
        ).mappings().first()
    assert row is not None
    assert "pi_abc" in row["body"]


def test_invalid_signature_returns_400(notifications_db, monkeypatch):
    def _raise(payload, sig_header, secret):
        raise stripe.error.SignatureVerificationError("bad signature", sig_header)

    monkeypatch.setattr(stripe.Webhook, "construct_event", _raise)

    client = _make_client()
    resp = client.post(
        "/notifications/webhooks/stripe",
        content=b'{"fake": "payload"}',
        headers={"Stripe-Signature": "t=1,v1=wrong"},
    )

    assert resp.status_code == 400


def test_missing_signature_header_returns_400(notifications_db, monkeypatch):
    def _raise(payload, sig_header, secret):
        raise ValueError("No signature header")

    monkeypatch.setattr(stripe.Webhook, "construct_event", _raise)

    client = _make_client()
    resp = client.post(
        "/notifications/webhooks/stripe",
        content=b'{"fake": "payload"}',
    )

    assert resp.status_code == 400


def test_other_event_types_are_ignored(notifications_db, monkeypatch):
    event = {"type": "charge.refunded", "data": {"object": {}}}
    monkeypatch.setattr(
        stripe.Webhook, "construct_event", lambda payload, sig_header, secret: event
    )

    client = _make_client()
    resp = client.post(
        "/notifications/webhooks/stripe",
        content=b'{"fake": "payload"}',
        headers={"Stripe-Signature": "t=1,v1=fake"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ignored", "type": "charge.refunded"}

    from sqlalchemy import text

    from modules.common.db import db_conn

    with db_conn() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM notifications")).scalar()
    assert count == 0
