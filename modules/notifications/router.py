"""Notifications HTTP surface: the Stripe webhook + owner status/retry
endpoints.

Mounted centrally into app/main.py via app.include_router(router).
"""
from __future__ import annotations

import logging
import secrets

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from app.config import get_settings
from modules.common.db import db_conn
from modules.notifications.service import handle_stripe_payment_succeeded, retry_notification

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["notifications"])

templates = Jinja2Templates(directory="modules/notifications/templates")

_security = HTTPBasic()


def require_owner(credentials: HTTPBasicCredentials = Depends(_security)) -> str:
    settings = get_settings()
    correct_user = secrets.compare_digest(credentials.username, settings.owner_ui_username)
    correct_pass = secrets.compare_digest(credentials.password, settings.owner_ui_password)
    if not (correct_user and correct_pass):
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"})
    return credentials.username


# ---------------------------------------------------------------------------
# Stripe webhook — no owner auth (Stripe signs the payload instead), same
# shape as app/main.py::calendly_webhook: read the raw body, verify a
# signature header against a shared secret, 400/401 on failure.
# ---------------------------------------------------------------------------


@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    settings = get_settings()
    raw = await request.body()
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        event = stripe.Webhook.construct_event(raw, sig_header, settings.stripe_webhook_secret)
    except (ValueError, stripe.error.SignatureVerificationError) as exc:
        logger.warning("Stripe webhook signature verification failed: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid webhook signature") from exc

    event_type = event.get("type") if isinstance(event, dict) else event["type"]
    if event_type == "payment_intent.succeeded":
        handle_stripe_payment_succeeded(event)
        return {"status": "ok"}

    return {"status": "ignored", "type": event_type}


# ---------------------------------------------------------------------------
# Owner-authed status/retry endpoints
# ---------------------------------------------------------------------------


def _fetch_recent(limit: int) -> list[dict]:
    with db_conn() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, type, title, body, payload, contact_id, delivered,
                       delivered_at, delivery_error, created_at
                FROM notifications
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            {"limit": limit},
        ).mappings()
        return [dict(row) for row in rows]


@router.get("/recent")
def recent(limit: int = 50, _owner: str = Depends(require_owner)):
    return {"notifications": _fetch_recent(limit)}


@router.get("/recent/html", response_class=HTMLResponse)
def recent_html(request: Request, limit: int = 50, _owner: str = Depends(require_owner)):
    return templates.TemplateResponse(
        "recent.html", {"request": request, "notifications": _fetch_recent(limit)}
    )


@router.post("/{notification_id}/retry")
def retry(notification_id: int, _owner: str = Depends(require_owner)):
    try:
        return retry_notification(notification_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
