"""FastAPI app: the owner-facing approval UI + the Calendly webhook receiver.

Run with: uvicorn app.main:app --host 0.0.0.0 --port 8000
The recurring jobs (sourcing, drafting, polling, digest) are wired up in
app/scheduler.py and started here on app startup.
"""
from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import SessionLocal, init_db
from app.integrations.calendly import (
    extract_event_uri,
    extract_invitee_email,
    extract_scheduled_time,
    verify_signature,
)
from app.models import Lead, LeadStatus, Meeting, Message, MessageStatus
from app.scheduler import start_scheduler
from app.services.approvals import approve_and_send, pending_approvals, reject
from app.services.notify import notify_meeting_booked
from modules.call_intelligence.router import router as call_intelligence_router
from modules.contracts.router import router as contracts_router
from modules.dialer.router import router as dialer_router
from modules.notifications.router import router as notifications_router
from modules.sync.router import router as sync_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory="app/web/templates")
security = HTTPBasic()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler = start_scheduler()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Outbound Lead-Gen Bot", lifespan=lifespan)

# JARVIS modules (dialer, call_intelligence, contracts, notifications) — each
# is self-contained under modules/<name>/, mounted here as the one shared
# integration point. See modules/README.md for the router.py/jobs.py contract.
app.include_router(dialer_router)
app.include_router(call_intelligence_router)
app.include_router(contracts_router)
app.include_router(notifications_router)
app.include_router(sync_router)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_owner(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    settings = get_settings()
    correct_user = secrets.compare_digest(credentials.username, settings.owner_ui_username)
    correct_pass = secrets.compare_digest(credentials.password, settings.owner_ui_password)
    if not (correct_user and correct_pass):
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"})
    return credentials.username


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db), _owner: str = Depends(require_owner)):
    counts = {
        "new_leads": db.query(Lead).filter(Lead.status == LeadStatus.NEW.value).count(),
        "queued_leads": db.query(Lead).filter(Lead.status == LeadStatus.QUEUED.value).count(),
        "contacted": db.query(Lead).filter(Lead.status == LeadStatus.CONTACTED.value).count(),
        "replied": db.query(Lead).filter(Lead.status == LeadStatus.REPLIED.value).count(),
        "interested": db.query(Lead).filter(Lead.status == LeadStatus.INTERESTED.value).count(),
        "meetings_booked": db.query(Meeting).count(),
        "pending_approvals": db.query(Message)
        .filter(Message.status == MessageStatus.PENDING_APPROVAL.value)
        .count(),
    }
    return templates.TemplateResponse("dashboard.html", {"request": request, "counts": counts})


@app.get("/approvals", response_class=HTMLResponse)
def list_approvals(
    request: Request, db: Session = Depends(get_db), _owner: str = Depends(require_owner)
):
    items = pending_approvals(db)
    return templates.TemplateResponse("approvals_list.html", {"request": request, "items": items})


@app.get("/approvals/{message_id}", response_class=HTMLResponse)
def view_approval(
    message_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _owner: str = Depends(require_owner),
):
    message = db.get(Message, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Not found")
    return templates.TemplateResponse(
        "approval_detail.html", {"request": request, "message": message, "lead": message.conversation.lead}
    )


@app.post("/approvals/{message_id}/approve")
def do_approve(
    message_id: int,
    body: str = Form(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _owner: str = Depends(require_owner),
):
    approve_and_send(db, settings, message_id, edited_body=body)
    return RedirectResponse("/approvals", status_code=303)


@app.post("/approvals/{message_id}/reject")
def do_reject(
    message_id: int,
    reason: str = Form(""),
    db: Session = Depends(get_db),
    _owner: str = Depends(require_owner),
):
    reject(db, message_id, reason)
    return RedirectResponse("/approvals", status_code=303)


@app.post("/webhooks/calendly")
async def calendly_webhook(
    request: Request, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
):
    raw = await request.body()
    signature = request.headers.get("Calendly-Webhook-Signature", "")
    if not verify_signature(raw, signature, settings.calendly_webhook_signing_key):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload = await request.json()
    if payload.get("event") != "invitee.created":
        return {"status": "ignored"}

    email = extract_invitee_email(payload).lower().strip()
    lead = db.query(Lead).filter(Lead.contact_email == email).first()
    if lead is None:
        logger.warning("Calendly booking from unknown email %s", email)
        return {"status": "unknown_lead"}

    from datetime import datetime

    scheduled_raw = extract_scheduled_time(payload)
    scheduled_time = None
    if scheduled_raw:
        try:
            scheduled_time = datetime.fromisoformat(scheduled_raw.replace("Z", "+00:00"))
        except ValueError:
            pass

    meeting = Meeting(
        lead_id=lead.id,
        calendly_event_uri=extract_event_uri(payload),
        scheduled_time=scheduled_time,
        status="scheduled",
    )
    db.add(meeting)
    lead.status = LeadStatus.MEETING_BOOKED.value
    db.commit()

    notify_meeting_booked(settings, lead, meeting)
    return {"status": "ok"}
