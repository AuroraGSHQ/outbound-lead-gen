"""The delivery sweep (notifications table -> SMS) and the Stripe
`payment_intent.succeeded` handler that raises a `payment_received`
notification. This is the only module that reads/delivers/marks
`notifications` rows delivered — see modules/README.md.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.config import Settings, get_settings
from modules.common.db import db_conn
from modules.common.notifications import create_notification
from modules.notifications import twilio_sms

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Give-up heuristic
#
# schema.sql's `notifications` table has no `attempt_count` column, so we
# track attempts by prepending a small counter onto `delivery_error` itself,
# e.g. "[attempt 3] <error text>". `_parse_attempt_count` reads that prefix
# back out (0 if absent/unparseable — i.e. never attempted, or an old/
# manually-written row). Once a row reaches MAX_DELIVERY_ATTEMPTS,
# deliver_pending_notifications() stops retrying it on future sweeps (it's
# counted as "skipped", not "failed") so a permanently-broken destination
# (bad OWNER_PHONE_NUMBER, revoked Twilio credentials, etc.) doesn't get
# hammered every few minutes forever. The row stays delivered=0 forever so
# it's still visible/flagged via GET /notifications/recent.
#
# POST /notifications/{id}/retry is the explicit owner override: it always
# attempts delivery once regardless of how many attempts have already been
# recorded, ignoring this gate entirely.
# ---------------------------------------------------------------------------
MAX_DELIVERY_ATTEMPTS = 5

_ATTEMPT_PREFIX_RE = re.compile(r"^\[attempt (\d+)\]\s*")


def _parse_attempt_count(delivery_error: str | None) -> int:
    if not delivery_error:
        return 0
    match = _ATTEMPT_PREFIX_RE.match(delivery_error)
    return int(match.group(1)) if match else 0


def _format_delivery_error(attempt: int, error: str) -> str:
    # Keep it bounded — delivery_error is TEXT NOT NULL DEFAULT '', no need
    # to let a giant provider error message grow unbounded across retries.
    return f"[attempt {attempt}] {error}"[:2000]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _attempt_delivery(settings: Settings, conn: Connection, row: dict[str, Any], attempt_count: int) -> bool:
    """Sends one SMS for `row` and writes the outcome straight back to the
    `notifications` row. Returns True on success. Whether this should run at
    all (the MAX_DELIVERY_ATTEMPTS gate) is the caller's decision — this
    function always attempts.
    """
    try:
        twilio_sms.send_sms(settings, row["title"], row["body"])
    except Exception as exc:  # noqa: BLE001 - any client/provider failure is a delivery failure
        new_attempt = attempt_count + 1
        conn.execute(
            text("UPDATE notifications SET delivery_error = :err WHERE id = :id"),
            {"err": _format_delivery_error(new_attempt, str(exc)), "id": row["id"]},
        )
        logger.warning(
            "Notification %s delivery failed (attempt %s/%s): %s",
            row["id"],
            new_attempt,
            MAX_DELIVERY_ATTEMPTS,
            exc,
        )
        return False

    conn.execute(
        text(
            """
            UPDATE notifications
            SET delivered = 1, delivered_at = :now, delivery_error = ''
            WHERE id = :id
            """
        ),
        {"now": _now_iso(), "id": row["id"]},
    )
    return True


def deliver_pending_notifications() -> dict[str, int]:
    """Scheduled job body (see jobs.py). Sends every undelivered notification
    via SMS, oldest first. Never raises on an individual delivery failure —
    that's recorded on the row and the sweep continues; jobs.py additionally
    wraps the whole call defensively per app/scheduler.py::_run_safely.

    Returns {"delivered": n, "failed": n, "skipped": n} — "skipped" counts
    rows that have already hit MAX_DELIVERY_ATTEMPTS and are left for a
    manual /retry instead of being retried automatically.
    """
    settings = get_settings()
    counts = {"delivered": 0, "failed": 0, "skipped": 0}

    with db_conn() as conn:
        rows = conn.execute(
            text("SELECT * FROM notifications WHERE delivered = 0 ORDER BY created_at")
        ).mappings()
        pending = [dict(row) for row in rows]

    for row in pending:
        attempt_count = _parse_attempt_count(row.get("delivery_error"))
        if attempt_count >= MAX_DELIVERY_ATTEMPTS:
            counts["skipped"] += 1
            continue

        with db_conn() as conn:
            success = _attempt_delivery(settings, conn, row, attempt_count)
        counts["delivered" if success else "failed"] += 1

    return counts


def retry_notification(notification_id: int) -> dict[str, Any]:
    """Owner-triggered manual retry for one notification (POST
    /notifications/{id}/retry) — bypasses the MAX_DELIVERY_ATTEMPTS give-up
    gate above and always attempts delivery once. Raises ValueError if the
    id doesn't exist.
    """
    settings = get_settings()
    with db_conn() as conn:
        row = conn.execute(
            text("SELECT * FROM notifications WHERE id = :id"), {"id": notification_id}
        ).mappings().first()
        if row is None:
            raise ValueError(f"No notification with id={notification_id}")
        row = dict(row)
        attempt_count = _parse_attempt_count(row.get("delivery_error"))
        success = _attempt_delivery(settings, conn, row, attempt_count)

    return {"id": notification_id, "delivered": success}


def handle_stripe_payment_succeeded(event: dict[str, Any]) -> None:
    """Given a Stripe `payment_intent.succeeded` event (the full event
    envelope, i.e. `{"type": ..., "data": {"object": {...}}}`, or the bare
    payment_intent object itself), raise a `payment_received` notification
    via the shared create_notification() helper.
    """
    payment_intent = (event.get("data") or {}).get("object")
    if payment_intent is None and event.get("object") == "payment_intent":
        payment_intent = event  # allow passing the payment_intent object directly (e.g. in tests)
    payment_intent = payment_intent or {}

    amount_cents = payment_intent.get("amount_received") or payment_intent.get("amount") or 0
    currency = (payment_intent.get("currency") or "usd").upper()
    amount = amount_cents / 100

    customer_name = ""
    charges = (payment_intent.get("charges") or {}).get("data") or []
    if charges:
        billing_details = charges[0].get("billing_details") or {}
        customer_name = billing_details.get("name") or ""
    if not customer_name:
        customer_name = payment_intent.get("receipt_email") or ""
    if not customer_name and payment_intent.get("customer"):
        customer_name = str(payment_intent["customer"])

    title = f"Payment received: {amount:,.2f} {currency}"
    body = f"{amount:,.2f} {currency} payment succeeded"
    if customer_name:
        body += f" from {customer_name}"
    body += f". Stripe payment_intent: {payment_intent.get('id', '')}"

    create_notification(
        type="payment_received",
        title=title,
        body=body,
        payload={
            "stripe_payment_intent_id": payment_intent.get("id", ""),
            "amount": amount,
            "currency": currency,
            "customer": customer_name,
        },
    )
