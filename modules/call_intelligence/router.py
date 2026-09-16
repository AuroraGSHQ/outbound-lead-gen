"""HTTP surface for call_intelligence: Zoom + Twilio webhooks, and an
owner-authed deadlines view.

Per modules/README.md's integration contract this is the only file (besides
jobs.py) that's mounted centrally into app/main.py — `router` is included
there via `app.include_router(router)`, not by this module.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from app.config import get_settings
from modules.call_intelligence.service import (
    create_call_log_from_twilio_recording,
    create_call_log_from_zoom,
)
from modules.common.db import db_conn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/call-intelligence", tags=["call_intelligence"])

templates = Jinja2Templates(directory="modules/call_intelligence/templates")

# --- Owner auth (local copy — importing app.main here would be circular;
# see the integration contract's exact snippet in the module's build brief) ---
_security = HTTPBasic()


def require_owner(credentials: HTTPBasicCredentials = Depends(_security)) -> str:
    settings = get_settings()
    correct_user = secrets.compare_digest(credentials.username, settings.owner_ui_username)
    correct_pass = secrets.compare_digest(credentials.password, settings.owner_ui_password)
    if not (correct_user and correct_pass):
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"})
    return credentials.username


# ---------------------------------------------------------------------
# Zoom webhook signature verification
# https://developers.zoom.us/docs/api/webhooks/#verify-webhook-events
# ---------------------------------------------------------------------
def verify_zoom_signature(raw_body: bytes, timestamp: str, signature: str, secret_token: str) -> bool:
    if not timestamp or not signature or not secret_token:
        return False
    message = f"v0:{timestamp}:{raw_body.decode('utf-8')}"
    expected = "v0=" + hmac.new(
        secret_token.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def build_zoom_url_validation_response(plain_token: str, secret_token: str) -> dict[str, str]:
    encrypted_token = hmac.new(
        secret_token.encode("utf-8"), plain_token.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return {"plainToken": plain_token, "encryptedToken": encrypted_token}


@router.post("/webhooks/zoom")
async def zoom_webhook(request: Request):
    settings = get_settings()
    raw = await request.body()

    try:
        payload: dict[str, Any] = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Zoom's one-time URL-validation handshake, sent when you first register
    # (or re-validate) the webhook endpoint — no x-zm-signature header on
    # this one, so it must be handled before signature verification.
    if payload.get("event") == "endpoint.url_validation":
        plain_token = (payload.get("payload") or {}).get("plainToken", "")
        if not plain_token or not settings.zoom_webhook_secret_token:
            raise HTTPException(status_code=400, detail="Missing plainToken or ZOOM_WEBHOOK_SECRET_TOKEN")
        return build_zoom_url_validation_response(plain_token, settings.zoom_webhook_secret_token)

    timestamp = request.headers.get("x-zm-request-timestamp", "")
    signature = request.headers.get("x-zm-signature", "")
    if not verify_zoom_signature(raw, timestamp, signature, settings.zoom_webhook_secret_token):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    event = payload.get("event", "")
    if event == "recording.completed":
        try:
            call_log_id = create_call_log_from_zoom(payload)
        except Exception:  # noqa: BLE001 - a bad/odd payload must not 500 the webhook
            logger.exception("zoom_webhook: failed to process recording.completed payload")
            return JSONResponse({"status": "error"}, status_code=200)
        return {"status": "ok", "call_log_id": call_log_id}

    return {"status": "ignored", "event": event}


# ---------------------------------------------------------------------
# Twilio recording-status-callback webhook
# ---------------------------------------------------------------------
def verify_twilio_signature(
    url: str, params: dict[str, Any], signature: str, auth_token: str
) -> bool:
    """Best-effort verification using twilio.request_validator.RequestValidator.
    `url` must be exactly the URL Twilio signed (scheme+host+path+query as
    configured in the Twilio console), which can be finicky behind a proxy/
    load balancer — see the TODO at the call site.
    """
    if not signature or not auth_token:
        return False
    from twilio.request_validator import RequestValidator

    validator = RequestValidator(auth_token)
    return validator.validate(url, params, signature)


@router.post("/webhooks/twilio-recording")
async def twilio_recording_webhook(request: Request):
    settings = get_settings()
    form = await request.form()
    params = dict(form)

    signature = request.headers.get("X-Twilio-Signature", "")
    if settings.twilio_auth_token and signature:
        # TODO(integration pass): str(request.url) reflects whatever scheme/host
        # this process sees, which may not match the public URL Twilio actually
        # signed if there's a reverse proxy in front (common on most hosts).
        # If verification starts failing in production, rebuild the URL from
        # X-Forwarded-Proto/X-Forwarded-Host or a configured public base URL
        # instead of trusting request.url directly.
        if not verify_twilio_signature(str(request.url), params, signature, settings.twilio_auth_token):
            raise HTTPException(status_code=401, detail="Invalid Twilio signature")
    else:
        logger.warning(
            "twilio_recording_webhook: skipping signature verification "
            "(missing X-Twilio-Signature header or TWILIO_AUTH_TOKEN)"
        )

    try:
        call_log_id = create_call_log_from_twilio_recording(params)
    except Exception:  # noqa: BLE001 - a bad/odd payload must not 500 the webhook
        logger.exception("twilio_recording_webhook: failed to process recording callback")
        return JSONResponse({"status": "error"}, status_code=200)

    return {"status": "ok", "call_log_id": call_log_id}


# ---------------------------------------------------------------------
# Owner-authed deadlines view
# ---------------------------------------------------------------------
@router.get("/deadlines")
def list_deadlines(request: Request, _owner: str = Depends(require_owner)):
    with db_conn() as conn:
        rows = conn.execute(
            text(
                """
                SELECT d.*, c.name AS contact_name, c.company_name
                FROM deadlines d
                LEFT JOIN contacts c ON c.id = d.contact_id
                WHERE d.completed = 0
                ORDER BY d.due_date ASC
                """
            )
        ).mappings().all()

    deadlines = [dict(row) for row in rows]

    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        from datetime import date

        return templates.TemplateResponse(
            "deadlines.html",
            {"request": request, "deadlines": deadlines, "today": date.today().isoformat()},
        )
    return {"deadlines": deadlines}
