"""Dialer HTTP surface: provider call-ended webhooks + an owner status page.

Mounted centrally into app/main.py via app.include_router(router).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from app.config import get_settings
from modules.common.db import db_conn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dialer", tags=["dialer"])

templates = Jinja2Templates(directory="modules/dialer/templates")

_security = HTTPBasic()


def require_owner(credentials: HTTPBasicCredentials = Depends(_security)) -> str:
    settings = get_settings()
    correct_user = secrets.compare_digest(credentials.username, settings.owner_ui_username)
    correct_pass = secrets.compare_digest(credentials.password, settings.owner_ui_password)
    if not (correct_user and correct_pass):
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"})
    return credentials.username


# ---------------------------------------------------------------------------
# Webhook: Vapi or Retell call-ended callback.
#
# Vapi signs with a shared "Server URL Secret" sent verbatim as the
# X-Vapi-Secret header (plain equality, not HMAC). Retell signs with
# X-Retell-Signature: an HMAC-SHA256 hex digest of the raw body using the
# webhook secret. Verification only runs when the matching secret is
# configured (vapi_webhook_secret / retell_webhook_secret) — if neither is
# set, the payload is trusted as-is and a warning is logged, same
# conservative fallback used for Twilio's signature check in
# call_intelligence's router.
# ---------------------------------------------------------------------------


def _verify_call_ended_signature(request: Request, raw_body: bytes) -> bool:
    settings = get_settings()
    vapi_secret_header = request.headers.get("x-vapi-secret", "")
    retell_signature = request.headers.get("x-retell-signature", "")

    if settings.vapi_webhook_secret and vapi_secret_header:
        return hmac.compare_digest(vapi_secret_header, settings.vapi_webhook_secret)

    if settings.retell_webhook_secret and retell_signature:
        expected = hmac.new(
            settings.retell_webhook_secret.encode("utf-8"), raw_body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, retell_signature)

    logger.warning(
        "Dialer webhook: no matching signing secret configured "
        "(vapi_webhook_secret/retell_webhook_secret) — skipping verification"
    )
    return True


def _extract_call_ended_fields(payload: dict) -> dict:
    """Normalize Vapi's and Retell's call-ended webhook shapes into a flat
    dict. Both providers wrap the interesting bits in slightly different
    envelopes; this pulls out whatever is present without assuming a single
    schema strictly.
    """
    # Vapi sends {"message": {"type": "end-of-call-report", "call": {...},
    # "transcript": "...", "summary": "...", "recordingUrl": "...",
    # "endedReason": "..."}}
    message = payload.get("message") if isinstance(payload.get("message"), dict) else None
    if message is not None:
        call = message.get("call") or {}
        return {
            "external_call_id": str(call.get("id") or message.get("call", {}).get("id") or ""),
            "transcript": message.get("transcript", "") or "",
            "outcome": message.get("endedReason", "") or message.get("status", "") or "completed",
            "recording_url": message.get("recordingUrl", "") or "",
            "summary": message.get("summary", "") or "",
        }

    # Retell sends {"event": "call_ended", "call": {"call_id": "...",
    # "transcript": "...", "recording_url": "...", "disconnection_reason": "...",
    # "call_analysis": {"call_summary": "..."}}}
    call = payload.get("call") if isinstance(payload.get("call"), dict) else payload
    call_analysis = call.get("call_analysis") or {}
    return {
        "external_call_id": str(call.get("call_id") or call.get("id") or ""),
        "transcript": call.get("transcript", "") or "",
        "outcome": call.get("disconnection_reason", "") or call.get("call_status", "") or "completed",
        "recording_url": call.get("recording_url", "") or "",
        "summary": call_analysis.get("call_summary", "") or "",
    }


@router.post("/webhooks/call-ended")
async def call_ended_webhook(request: Request):
    raw = await request.body()
    if not _verify_call_ended_signature(request, raw):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload = json.loads(raw)
    fields = _extract_call_ended_fields(payload)
    external_call_id = fields["external_call_id"]

    if not external_call_id:
        logger.warning("Dialer webhook: call-ended payload missing an external call id: %s", payload)
        return {"status": "ignored", "reason": "missing_external_call_id"}

    with db_conn() as conn:
        result = conn.execute(
            text(
                """
                UPDATE call_logs
                SET transcript = :transcript,
                    outcome = :outcome,
                    recording_url = :recording_url,
                    summary = CASE WHEN :summary != '' THEN :summary ELSE summary END
                WHERE external_call_id = :external_call_id
                """
            ),
            {
                "transcript": fields["transcript"],
                "outcome": fields["outcome"],
                "recording_url": fields["recording_url"],
                "summary": fields["summary"],
                "external_call_id": external_call_id,
            },
        )

    if result.rowcount == 0:
        logger.warning(
            "Dialer webhook: no call_logs row found for external_call_id=%s", external_call_id
        )
        return {"status": "no_matching_call_log", "external_call_id": external_call_id}

    return {"status": "ok", "external_call_id": external_call_id}


@router.get("/status")
def status(_owner: str = Depends(require_owner)):
    with db_conn() as conn:
        recent_rows = conn.execute(
            text(
                """
                SELECT cl.id, cl.contact_id, c.name AS contact_name, c.segment,
                       cl.call_type, cl.direction, cl.external_call_id, cl.outcome,
                       cl.summary, cl.started_at, cl.created_at
                FROM call_logs cl
                LEFT JOIN contacts c ON c.id = cl.contact_id
                WHERE cl.call_type = 'ai_outbound'
                ORDER BY cl.created_at DESC
                LIMIT 50
                """
            )
        ).mappings()
        recent = [dict(row) for row in recent_rows]

        outcome_rows = conn.execute(
            text(
                """
                SELECT outcome, COUNT(*) AS count
                FROM call_logs
                WHERE call_type = 'ai_outbound'
                GROUP BY outcome
                ORDER BY count DESC
                """
            )
        ).all()
        counts_by_outcome = {row[0]: row[1] for row in outcome_rows}

    return {
        "recent_calls": recent,
        "counts_by_outcome": counts_by_outcome,
    }


@router.get("/status/html", response_class=HTMLResponse)
def status_html(request: Request, _owner: str = Depends(require_owner)):
    # Duplicates the query logic in status() above rather than calling it
    # directly, to avoid depending on FastAPI's dependency-injection
    # internals when invoking one route handler from another.
    with db_conn() as conn:
        recent_rows = conn.execute(
            text(
                """
                SELECT cl.id, cl.contact_id, c.name AS contact_name, c.segment,
                       cl.call_type, cl.direction, cl.external_call_id, cl.outcome,
                       cl.summary, cl.started_at, cl.created_at
                FROM call_logs cl
                LEFT JOIN contacts c ON c.id = cl.contact_id
                WHERE cl.call_type = 'ai_outbound'
                ORDER BY cl.created_at DESC
                LIMIT 50
                """
            )
        ).mappings()
        recent = [dict(row) for row in recent_rows]

        outcome_rows = conn.execute(
            text(
                """
                SELECT outcome, COUNT(*) AS count
                FROM call_logs
                WHERE call_type = 'ai_outbound'
                GROUP BY outcome
                ORDER BY count DESC
                """
            )
        ).all()
        counts_by_outcome = {row[0]: row[1] for row in outcome_rows}

    return templates.TemplateResponse(
        "status.html",
        {"request": request, "recent_calls": recent, "counts_by_outcome": counts_by_outcome},
    )
