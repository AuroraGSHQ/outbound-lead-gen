"""Contracts module routes: owner-authed review queue + provider webhooks.

Mounted centrally into app/main.py via `app.include_router(router)` per the
modules/README.md integration contract. Nothing here imports from
app.main (that would be circular) or from another modules/<name> package.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from app.config import get_settings
from modules.common.db import db_conn
from modules.contracts.service import (
    ContractNotFoundError,
    ContractStateError,
    approve_contract,
    create_contract_for_closed_deal,
    mark_signed_by_external_id,
    reject_contract,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/contracts", tags=["contracts"])

templates = Jinja2Templates(directory="modules/contracts/templates")

_security = HTTPBasic()


def require_owner(credentials: HTTPBasicCredentials = Depends(_security)) -> str:
    settings = get_settings()
    correct_user = secrets.compare_digest(credentials.username, settings.owner_ui_username)
    correct_pass = secrets.compare_digest(credentials.password, settings.owner_ui_password)
    if not (correct_user and correct_pass):
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"})
    return credentials.username


def _row_to_dict(row) -> dict:
    d = dict(row)
    try:
        d["filled_data"] = json.loads(d.get("filled_data") or "{}")
    except (TypeError, ValueError):
        pass
    return d


# ----------------------------------------------------------------------
# Owner-authed review queue
# ----------------------------------------------------------------------
@router.get("/pending")
def list_pending(request: Request, _owner: str = Depends(require_owner)):
    with db_conn() as conn:
        rows = conn.execute(
            text(
                "SELECT * FROM contracts WHERE status = 'pending_approval' ORDER BY created_at DESC"
            )
        ).mappings().all()
    items = [_row_to_dict(r) for r in rows]

    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        return templates.TemplateResponse(
            "pending.html", {"request": request, "items": items}
        )
    return JSONResponse(items)


@router.get("/{contract_id}")
def get_contract_detail(contract_id: int, request: Request, _owner: str = Depends(require_owner)):
    with db_conn() as conn:
        row = conn.execute(
            text("SELECT * FROM contracts WHERE id = :id"), {"id": contract_id}
        ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Contract not found")
    item = _row_to_dict(row)

    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        return templates.TemplateResponse(
            "detail.html", {"request": request, "item": item}
        )
    return JSONResponse(item)


@router.post("/{contract_id}/approve")
def do_approve(contract_id: int, _owner: str = Depends(require_owner)):
    try:
        approve_contract(contract_id)
    except ContractNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ContractStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "sent", "contract_id": contract_id}


@router.post("/{contract_id}/reject")
def do_reject(contract_id: int, reason: str = Form(""), _owner: str = Depends(require_owner)):
    try:
        reject_contract(contract_id, reason)
    except ContractNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ContractStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "rejected", "contract_id": contract_id}


# ----------------------------------------------------------------------
# Manual trigger — standalone stand-in for the (not-yet-wired) automatic
# "deal closed" trigger. See router docstring / module README for why this
# module can't call into call_intelligence/dialer directly.
# ----------------------------------------------------------------------
@router.post("/create-from-contact/{contact_id}")
def create_from_contact(
    contact_id: int,
    price: str = Form(""),
    scope: str = Form(""),
    deadline: str = Form(""),
    call_log_id: int | None = Form(None),
    _owner: str = Depends(require_owner),
):
    deal_fields = {}
    if price:
        deal_fields["price"] = price
    if scope:
        deal_fields["scope"] = scope
    if deadline:
        deal_fields["deadline"] = deadline

    try:
        contract_id = create_contract_for_closed_deal(contact_id, call_log_id, deal_fields)
    except ContractNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "pending_approval", "contract_id": contract_id}


# ----------------------------------------------------------------------
# Provider webhooks — no owner auth (these are provider -> us callbacks).
# ----------------------------------------------------------------------
@router.post("/webhooks/pandadoc")
async def pandadoc_webhook(request: Request):
    """PandaDoc sends webhook events as a JSON array of event objects,
    signed with an HMAC-SHA256 hex digest of the raw body (shared secret
    configured per-webhook in the PandaDoc dashboard) in the
    PandaDoc-Signature header. Verification only runs when
    pandadoc_webhook_shared_key is set; otherwise the payload is trusted
    as-is and a warning is logged (same conservative fallback used
    elsewhere, e.g. Twilio's signature check in call_intelligence).
    """
    settings = get_settings()
    raw = await request.body()
    signature = request.headers.get("PandaDoc-Signature", "")

    if settings.pandadoc_webhook_shared_key:
        expected = hmac.new(
            settings.pandadoc_webhook_shared_key.encode("utf-8"), raw, hashlib.sha256
        ).hexdigest()
        if not signature or not hmac.compare_digest(expected, signature):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
    else:
        logger.warning(
            "pandadoc_webhook: PANDADOC_WEBHOOK_SHARED_KEY not set — skipping signature verification"
        )

    body = json.loads(raw)
    events = body if isinstance(body, list) else [body]
    for event in events:
        event_type = event.get("event") or event.get("event_type") or ""
        data = event.get("data") or {}
        document_id = data.get("id") or event.get("id") or ""
        status = data.get("status") or event.get("status") or ""
        if not document_id:
            continue
        if event_type == "document_state_changed" and status == "document.completed":
            mark_signed_by_external_id(str(document_id))
        elif event_type in ("document.completed",):
            mark_signed_by_external_id(str(document_id))
    return {"status": "ok"}


@router.post("/webhooks/docusign")
async def docusign_webhook(request: Request):
    """DocuSign Connect posts an XML or JSON payload depending on how the
    Connect configuration is set up; this handler assumes the JSON
    (`aXML`-free) "Connect JSON" format, which nests the envelope status at
    `data.envelopeSummary.status` (Connect v2) or top-level `status` for
    simpler configurations — we check both.

    DocuSign Connect signs the raw body with HMAC-SHA256, base64-encoded,
    in the X-DocuSign-Signature-1 header (per-connection shared secret).
    Verification only runs when docusign_connect_hmac_key is set; otherwise
    the payload is trusted as-is and a warning is logged.
    """
    settings = get_settings()
    raw = await request.body()
    signature = request.headers.get("X-DocuSign-Signature-1", "")

    if settings.docusign_connect_hmac_key:
        expected = base64.b64encode(
            hmac.new(settings.docusign_connect_hmac_key.encode("utf-8"), raw, hashlib.sha256).digest()
        ).decode("utf-8")
        if not signature or not hmac.compare_digest(expected, signature):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
    else:
        logger.warning(
            "docusign_webhook: DOCUSIGN_CONNECT_HMAC_KEY not set — skipping signature verification"
        )

    body = json.loads(raw)
    envelope_id = (
        body.get("envelopeId")
        or (body.get("data") or {}).get("envelopeId")
        or ""
    )
    status = (
        body.get("status")
        or (body.get("data") or {}).get("envelopeSummary", {}).get("status")
        or ""
    )
    if envelope_id and status.lower() == "completed":
        mark_signed_by_external_id(str(envelope_id))
    return {"status": "ok"}
