"""FastAPI app: the team-facing dashboard (approvals, intake, scanner, ads,
referrals, metrics, team) + the Calendly webhook receiver.

Run with: uvicorn app.main:app --host 0.0.0.0 --port 8000
The recurring jobs (sourcing, drafting, polling, digest, and the newer
agents) are wired up in app/scheduler.py and started here on app startup.
"""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
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
from app.models import (
    ActionItem,
    AdCampaign,
    Client,
    ClientStatus,
    CompetitorNoteType,
    CrmProvider,
    IntakeCall,
    Lead,
    LeadStatus,
    Meeting,
    Message,
    MessageStatus,
    ScanResult,
    SourcingRequest,
    User,
    UserRole,
)
from app.scheduler import AGENT_JOBS, JOB_FUNCTIONS, get_scheduler, start_scheduler
from app.services import (
    actions,
    ads,
    agent_toggles,
    competitor_watch,
    content,
    crm_sync,
    intake,
    lead_import,
    metrics,
    onboarding,
    proposals,
    recon,
    referrals,
    reviews,
    scanner,
    sourcing_requests,
    users,
)
from app.services.approvals import approve_and_send, pending_approvals, reject
from app.services.notify import notify_meeting_booked
from app.web.auth import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    NotAuthenticated,
    make_session_cookie_value,
    require_login,
    require_roles,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory="app/web/templates")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seed_session = SessionLocal()
    try:
        users.ensure_seed_owner(seed_session, get_settings())
    finally:
        seed_session.close()
    scheduler = start_scheduler()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Aurora Growth OS", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/web/static"), name="static")


@app.exception_handler(NotAuthenticated)
async def _redirect_to_login(request: Request, exc: NotAuthenticated):
    return RedirectResponse(f"/login?next={request.url.path}", status_code=303)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/offline", response_class=HTMLResponse)
def offline_page(request: Request):
    return templates.TemplateResponse("offline.html", {"request": request})


# --- Auth ------------------------------------------------------------------


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/"):
    return templates.TemplateResponse("login.html", {"request": request, "next": next, "error": ""})


@app.post("/login")
def do_login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    user = users.authenticate(db, email, password)
    if user is None:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "next": next, "error": "Wrong email or password."},
            status_code=401,
        )
    response = RedirectResponse(next or "/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        make_session_cookie_value(settings, user.id),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
    )
    return response


@app.post("/logout")
def do_logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


# --- Dashboard ---------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db), user: User = Depends(require_login)):
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
        "open_actions": db.query(ActionItem).filter(ActionItem.status != "done").count(),
        "clients_active": db.query(Client).filter(Client.status == ClientStatus.ACTIVE.value).count(),
        "unverified_scans": db.query(ScanResult).filter(ScanResult.verified.is_(False)).count(),
        "open_sourcing_requests": db.query(SourcingRequest).filter(SourcingRequest.status == "requested").count(),
        "system_alerts": db.query(ActionItem)
        .filter(ActionItem.category == "system", ActionItem.status != "done")
        .count(),
    }
    snapshot = metrics.latest_snapshot(db)
    return templates.TemplateResponse(
        "dashboard.html", {"request": request, "counts": counts, "user": user, "snapshot": snapshot}
    )


# --- Approvals (Scribe) ------------------------------------------------------


@app.get("/approvals", response_class=HTMLResponse)
def list_approvals(
    request: Request, db: Session = Depends(get_db), user: User = Depends(require_roles("sales"))
):
    items = pending_approvals(db)
    return templates.TemplateResponse("approvals_list.html", {"request": request, "items": items, "user": user})


@app.get("/approvals/{message_id}", response_class=HTMLResponse)
def view_approval(
    message_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("sales")),
):
    message = db.get(Message, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Not found")
    return templates.TemplateResponse(
        "approval_detail.html",
        {"request": request, "message": message, "lead": message.conversation.lead, "user": user},
    )


@app.post("/approvals/{message_id}/approve")
def do_approve(
    message_id: int,
    body: str = Form(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: User = Depends(require_roles("sales")),
):
    approve_and_send(db, settings, message_id, edited_body=body)
    return RedirectResponse("/approvals", status_code=303)


@app.post("/approvals/{message_id}/reject")
def do_reject(
    message_id: int,
    reason: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("sales")),
):
    reject(db, message_id, reason)
    return RedirectResponse("/approvals", status_code=303)


# --- Users (owner only) -------------------------------------------------------


@app.get("/users", response_class=HTMLResponse)
def list_users_page(
    request: Request, db: Session = Depends(get_db), user: User = Depends(require_roles())
):
    return templates.TemplateResponse(
        "users_list.html", {"request": request, "team": users.list_users(db), "user": user, "roles": [r.value for r in UserRole]}
    )


@app.post("/users/new")
def create_user_route(
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form(UserRole.OPS.value),
    notify_system_alerts: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles()),
):
    new_user = users.create_user(db, name=name, email=email, password=password, role=role)
    new_user.notify_system_alerts = bool(notify_system_alerts)
    db.commit()
    return RedirectResponse("/users", status_code=303)


@app.post("/users/{user_id}/notify-toggle")
def toggle_user_notify(
    user_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles())
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Not found")
    target.notify_system_alerts = not target.notify_system_alerts
    db.commit()
    return RedirectResponse("/users", status_code=303)


# --- Leads (for picking who to start an intake on) ---------------------------


@app.get("/leads", response_class=HTMLResponse)
def list_leads(request: Request, q: str = "", db: Session = Depends(get_db), user: User = Depends(require_roles("sales"))):
    query = db.query(Lead)
    if q:
        like = f"%{q}%"
        query = query.filter((Lead.company_name.ilike(like)) | (Lead.contact_email.ilike(like)))
    leads = query.order_by(Lead.created_at.desc()).limit(100).all()
    return templates.TemplateResponse("leads_list.html", {"request": request, "leads": leads, "q": q, "user": user})


@app.post("/leads/{lead_id}/recon")
def run_recon(
    lead_id: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: User = Depends(require_roles("sales")),
):
    recon.prep_meeting_brief(db, settings, lead_id)
    return RedirectResponse("/actions", status_code=303)


# --- Sourcing requests (Hermes / Vibe Prospecting) -----------------------------


@app.get("/sourcing", response_class=HTMLResponse)
def list_sourcing(request: Request, db: Session = Depends(get_db), user: User = Depends(require_roles("sales"))):
    return templates.TemplateResponse(
        "sourcing_list.html", {"request": request, "requests": sourcing_requests.list_requests(db), "user": user}
    )


@app.get("/sourcing/new", response_class=HTMLResponse)
def new_sourcing_request_form(request: Request, user: User = Depends(require_roles("sales"))):
    return templates.TemplateResponse("sourcing_new.html", {"request": request, "user": user})


@app.post("/sourcing/new")
def create_sourcing_request(
    industry: str = Form(""),
    job_titles: str = Form(""),
    locations: str = Form(""),
    company_size: str = Form(""),
    company_revenue: str = Form(""),
    keywords: str = Form(""),
    number_of_results: int = Form(30),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("sales")),
):
    criteria = {
        "industry": industry,
        "job_titles": job_titles,
        "locations": locations,
        "company_size": company_size,
        "company_revenue": company_revenue,
        "keywords": keywords,
        "number_of_results": number_of_results,
        "notes": notes,
    }
    record = sourcing_requests.create_request(db, requested_by_user_id=user.id, criteria=criteria)
    return RedirectResponse(f"/sourcing/{record.id}", status_code=303)


@app.get("/sourcing/{request_id}", response_class=HTMLResponse)
def view_sourcing_request(
    request_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_roles("sales"))
):
    record = sourcing_requests.get_request(db, request_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Not found")
    return templates.TemplateResponse(
        "sourcing_detail.html",
        {"request": request, "record": record, "prompt": sourcing_requests.build_prompt(record), "user": user},
    )


@app.post("/sourcing/{request_id}/upload")
async def upload_sourcing_csv(
    request_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("sales")),
):
    raw = await file.read()
    csv_text = raw.decode("utf-8-sig", errors="replace")
    sourcing_requests.store_pending_csv(db, request_id, csv_text)
    return RedirectResponse(f"/sourcing/{request_id}/map", status_code=303)


@app.get("/sourcing/{request_id}/map", response_class=HTMLResponse)
def map_sourcing_csv(
    request_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_roles("sales"))
):
    record = sourcing_requests.get_request(db, request_id)
    if record is None or not record.pending_csv:
        raise HTTPException(status_code=404, detail="No uploaded file waiting to be mapped for this request.")
    headers = lead_import.parse_headers(record.pending_csv)
    suggestion = lead_import.suggest_mapping(headers)
    return templates.TemplateResponse(
        "sourcing_map.html",
        {
            "request": request,
            "record": record,
            "headers": headers,
            "suggestion": suggestion,
            "fields": lead_import.LEAD_FIELDS,
            "user": user,
        },
    )


@app.post("/sourcing/{request_id}/map")
async def confirm_sourcing_mapping(
    request_id: int,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: User = Depends(require_roles("sales")),
):
    record = sourcing_requests.get_request(db, request_id)
    if record is None or not record.pending_csv:
        raise HTTPException(status_code=404, detail="No uploaded file waiting to be mapped for this request.")
    form = await request.form()
    mapping = {
        field: form.get(f"map_{field}", "")
        for field in lead_import.LEAD_FIELDS
        if form.get(f"map_{field}")
    }
    stats = lead_import.import_leads_from_csv(db, settings, record.pending_csv, mapping)
    sourcing_requests.mark_fulfilled(db, request_id, stats["imported"])
    return RedirectResponse(f"/sourcing/{request_id}", status_code=303)


# --- Intake (Concierge) -------------------------------------------------------


@app.get("/intake", response_class=HTMLResponse)
def list_intake(request: Request, db: Session = Depends(get_db), user: User = Depends(require_roles("sales"))):
    return templates.TemplateResponse(
        "intake_list.html", {"request": request, "intakes": intake.list_intakes(db), "user": user}
    )


@app.get("/intake/new", response_class=HTMLResponse)
def new_intake_form(
    request: Request, lead_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(require_roles("sales"))
):
    lead = db.get(Lead, lead_id) if lead_id else None
    return templates.TemplateResponse("intake_new.html", {"request": request, "lead": lead, "user": user})


@app.post("/intake/new")
def create_intake_route(
    request: Request,
    lead_id: int = Form(0),
    contact_name: str = Form(""),
    contact_email: str = Form(""),
    contact_title: str = Form(""),
    company_name: str = Form(""),
    raw_notes: str = Form(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: User = Depends(require_roles("sales")),
):
    lead = db.get(Lead, lead_id) if lead_id else None
    if lead is None:
        if not contact_email:
            raise HTTPException(status_code=400, detail="A contact email is required for a new prospect.")
        email = contact_email.lower().strip()
        lead = db.query(Lead).filter(Lead.contact_email == email).first()
        if lead is None:
            lead = Lead(
                company_name=company_name or "Unknown",
                contact_name=contact_name,
                contact_title=contact_title,
                contact_email=email,
                source="intro_call",
                status=LeadStatus.REPLIED.value,
            )
            db.add(lead)
            db.commit()
            db.refresh(lead)
    record = intake.create_intake(db, settings, lead.id, raw_notes, user)
    return RedirectResponse(f"/intake/{record.id}", status_code=303)


@app.get("/intake/{intake_id}", response_class=HTMLResponse)
def view_intake(
    intake_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_roles("sales"))
):
    record = db.get(IntakeCall, intake_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Not found")
    action_items = db.query(ActionItem).filter(ActionItem.client_id == record.client_id).all()
    return templates.TemplateResponse(
        "intake_detail.html", {"request": request, "intake": record, "action_items": action_items, "user": user}
    )


# --- Action-item board ---------------------------------------------------------


@app.get("/actions", response_class=HTMLResponse)
def list_actions(
    request: Request,
    status: str = "",
    category: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    items = actions.list_action_items(db, status=status or None, category=category or None)
    return templates.TemplateResponse(
        "actions_list.html", {"request": request, "items": items, "status": status, "category": category, "user": user}
    )


@app.post("/actions/{action_id}/execute")
def execute_action(
    action_id: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: User = Depends(require_login),
):
    actions.execute_action_item(db, settings, action_id)
    return RedirectResponse("/actions", status_code=303)


@app.post("/actions/{action_id}/done")
def done_action(
    action_id: int, note: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require_login)
):
    actions.mark_done(db, action_id, note)
    return RedirectResponse("/actions", status_code=303)


@app.post("/actions/{action_id}/assign")
def assign_action(
    action_id: int,
    assignee_user_id: int = Form(0),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    actions.assign(db, action_id, assignee_user_id or None)
    return RedirectResponse("/actions", status_code=303)


# --- Clients ---------------------------------------------------------------------


@app.get("/clients", response_class=HTMLResponse)
def list_clients(request: Request, db: Session = Depends(get_db), user: User = Depends(require_login)):
    clients = db.query(Client).order_by(Client.created_at.desc()).all()
    return templates.TemplateResponse("clients_list.html", {"request": request, "clients": clients, "user": user})


@app.get("/clients/{client_id}", response_class=HTMLResponse)
def view_client(
    client_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_login)
):
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Not found")
    client_referrals = db.query(referrals.ReferralRecord).filter(referrals.ReferralRecord.client_id == client_id).all()
    action_items = db.query(ActionItem).filter(ActionItem.client_id == client_id).all()
    crm_logs = crm_sync.list_logs(db, client_id=client_id)
    return templates.TemplateResponse(
        "client_detail.html",
        {
            "request": request,
            "client": client,
            "referrals": client_referrals,
            "action_items": action_items,
            "crm_logs": crm_logs,
            "user": user,
        },
    )


@app.post("/clients/{client_id}/update")
def update_client(
    client_id: int,
    status: str = Form(...),
    tier: str = Form(""),
    monthly_fee: float = Form(0.0),
    start_date: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("sales", "ops")),
):
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Not found")
    was_active = client.status == ClientStatus.ACTIVE.value
    client.status = status
    client.tier = tier
    client.monthly_fee = monthly_fee
    if start_date:
        client.start_date = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
    db.commit()
    if status == ClientStatus.ACTIVE.value and not was_active:
        onboarding.create_onboarding_checklist(db, client)
    return RedirectResponse(f"/clients/{client_id}", status_code=303)


@app.post("/clients/{client_id}/proposal")
def generate_client_proposal(
    client_id: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: User = Depends(require_roles("sales")),
):
    proposals.generate_proposal(db, settings, client_id)
    return RedirectResponse("/actions", status_code=303)


@app.post("/clients/{client_id}/crm-config")
def update_client_crm_config(
    client_id: int,
    crm_provider: str = Form(CrmProvider.NONE.value),
    crm_api_key: str = Form(""),
    crm_board_id: str = Form(""),
    crm_location_id: str = Form(""),
    crm_column_map: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("sales", "ops")),
):
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Not found")
    client.crm_provider = crm_provider
    if crm_api_key.strip():  # blank means "leave the existing key alone"
        client.crm_api_key = crm_api_key.strip()
    config: dict = {}
    if crm_board_id.strip():
        config["board_id"] = crm_board_id.strip()
    if crm_location_id.strip():
        config["location_id"] = crm_location_id.strip()
    if crm_column_map.strip():
        try:
            config["column_map"] = json.loads(crm_column_map)
        except ValueError:
            raise HTTPException(status_code=400, detail="Column map must be valid JSON, e.g. {\"email\": \"text_1\"}")
    client.crm_config = config
    db.commit()
    return RedirectResponse(f"/clients/{client_id}", status_code=303)


# --- CRM sync (Charon) ---------------------------------------------------------


@app.get("/crm-sync", response_class=HTMLResponse)
def new_crm_sync(request: Request, db: Session = Depends(get_db), user: User = Depends(require_roles("sales", "ops"))):
    clients = db.query(Client).order_by(Client.company_name.asc()).all()
    return templates.TemplateResponse(
        "crm_sync_new.html",
        {"request": request, "clients": clients, "logs": crm_sync.list_logs(db)[:20], "user": user},
    )


@app.post("/crm-sync/parse", response_class=HTMLResponse)
def parse_crm_sync(
    request: Request,
    client_id: int = Form(...),
    raw_text: str = Form(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: User = Depends(require_roles("sales", "ops")),
):
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Not found")
    fields = crm_sync.parse_lead_details(settings, raw_text)
    return templates.TemplateResponse(
        "crm_sync_review.html", {"request": request, "client": client, "fields": fields, "user": user}
    )


@app.post("/crm-sync/push")
def push_crm_sync(
    client_id: int = Form(...),
    full_name: str = Form(""),
    first_name: str = Form(""),
    last_name: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    company: str = Form(""),
    source: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("sales", "ops")),
):
    fields = {
        "full_name": full_name, "first_name": first_name, "last_name": last_name,
        "email": email, "phone": phone, "company": company, "source": source, "notes": notes,
    }
    crm_sync.push_to_crm(db, client_id, fields, created_by_user_id=user.id)
    return RedirectResponse("/crm-sync", status_code=303)


# --- Scanner -----------------------------------------------------------------


@app.get("/scanner", response_class=HTMLResponse)
def list_scanner(request: Request, db: Session = Depends(get_db), user: User = Depends(require_roles("marketing"))):
    return templates.TemplateResponse(
        "scanner_list.html", {"request": request, "results": scanner.list_scan_results(db), "user": user}
    )


@app.post("/scanner/new")
def new_scan(
    domain: str = Form(...),
    company_name: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("marketing")),
):
    target = scanner.queue_target(db, domain=domain, company_name=company_name, added_by_user_id=user.id)
    scanner.run_scan(db, target.id)
    return RedirectResponse(f"/scanner/{target.id}", status_code=303)


@app.get("/scanner/{scan_id}", response_class=HTMLResponse)
def view_scan(
    scan_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_roles("marketing"))
):
    scan = db.get(ScanResult, scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="Not found")
    return templates.TemplateResponse("scanner_detail.html", {"request": request, "scan": scan, "user": user})


@app.post("/scanner/{scan_id}/verify")
def verify_scan(
    scan_id: int,
    notes: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("marketing")),
):
    scanner.mark_verified(db, scan_id, user.id, notes)
    return RedirectResponse(f"/scanner/{scan_id}", status_code=303)


@app.post("/scanner/{scan_id}/draft-outreach")
def draft_scan_outreach(
    scan_id: int,
    contact_name: str = Form(""),
    contact_email: str = Form(...),
    contact_title: str = Form(""),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: User = Depends(require_roles("marketing", "sales")),
):
    scanner.draft_fault_led_outreach(
        db, settings, scan_id, contact_name=contact_name, contact_email=contact_email, contact_title=contact_title
    )
    return RedirectResponse("/approvals", status_code=303)


# --- Referrals (Connector) -----------------------------------------------------


@app.get("/referrals", response_class=HTMLResponse)
def list_referrals_page(
    request: Request, db: Session = Depends(get_db), user: User = Depends(require_roles("ops"))
):
    clients = db.query(Client).order_by(Client.company_name.asc()).all()
    return templates.TemplateResponse(
        "referrals_list.html", {"request": request, "records": referrals.list_referrals(db), "clients": clients, "user": user}
    )


@app.post("/referrals/new")
def log_referral(
    client_id: int = Form(...),
    referrer_name: str = Form(""),
    referred_name: str = Form(""),
    referred_email: str = Form(""),
    status: str = Form("asked"),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("ops")),
):
    referrals.log_referral_outcome(
        db,
        client_id=client_id,
        referrer_name=referrer_name,
        referred_name=referred_name,
        referred_email=referred_email,
        status=status,
        notes=notes,
    )
    return RedirectResponse("/referrals", status_code=303)


@app.post("/referrals/{referral_id}/status")
def update_referral(
    referral_id: int,
    status: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("ops")),
):
    referrals.update_referral_status(db, referral_id, status)
    return RedirectResponse("/referrals", status_code=303)


# --- Ads (Promoter) -------------------------------------------------------------


@app.get("/ads", response_class=HTMLResponse)
def list_ads(request: Request, db: Session = Depends(get_db), user: User = Depends(require_roles("marketing"))):
    return templates.TemplateResponse("ads_list.html", {"request": request, "campaigns": ads.list_campaigns(db), "user": user})


@app.post("/ads/generate")
def generate_ads(
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings), user: User = Depends(require_roles("marketing"))
):
    ads.generate_monthly_brief(db, settings)
    return RedirectResponse("/ads", status_code=303)


@app.get("/ads/{campaign_id}", response_class=HTMLResponse)
def view_ad_campaign(
    campaign_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_roles("marketing"))
):
    campaign = db.get(AdCampaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Not found")
    return templates.TemplateResponse("ads_detail.html", {"request": request, "campaign": campaign, "user": user})


@app.post("/ads/{campaign_id}/publish")
def publish_ad(
    campaign_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("marketing"))
):
    ads.mark_published(db, campaign_id)
    return RedirectResponse(f"/ads/{campaign_id}", status_code=303)


@app.post("/content/generate")
def generate_content(
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings), user: User = Depends(require_roles("marketing"))
):
    content.generate_quarterly_extract(db, settings)
    return RedirectResponse("/actions", status_code=303)


# --- Reviews (Echo) -------------------------------------------------------------


@app.get("/reviews", response_class=HTMLResponse)
def list_reviews(request: Request, db: Session = Depends(get_db), user: User = Depends(require_roles("sales", "ops"))):
    items = (
        db.query(ActionItem)
        .filter(ActionItem.category == "reviews")
        .order_by(ActionItem.created_at.desc())
        .all()
    )
    clients = db.query(Client).order_by(Client.company_name.asc()).all()
    return templates.TemplateResponse("reviews_list.html", {"request": request, "items": items, "clients": clients, "user": user})


@app.post("/reviews/log")
def log_review(
    client_id: int = Form(0),
    review_text: str = Form(...),
    rating: str = Form("5"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: User = Depends(require_roles("sales", "ops")),
):
    reviews.log_review_and_draft_response(
        db, settings, client_id=client_id or None, review_text=review_text, rating=rating
    )
    return RedirectResponse("/reviews", status_code=303)


# --- Competitor watch (Eris / Astraea) -------------------------------------------


@app.get("/competitors", response_class=HTMLResponse)
def list_competitors(request: Request, db: Session = Depends(get_db), user: User = Depends(require_roles("marketing", "sales"))):
    return templates.TemplateResponse(
        "competitors_list.html",
        {"request": request, "notes": competitor_watch.list_notes(db), "user": user, "note_types": [t.value for t in CompetitorNoteType]},
    )


@app.post("/competitors/new")
def log_competitor_note(
    competitor_name: str = Form(...),
    note_type: str = Form(CompetitorNoteType.AD.value),
    source_url: str = Form(""),
    observed_text: str = Form(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: User = Depends(require_roles("marketing", "sales")),
):
    competitor_watch.log_note(
        db,
        settings,
        competitor_name=competitor_name,
        note_type=note_type,
        source_url=source_url,
        observed_text=observed_text,
        logged_by_user_id=user.id,
    )
    return RedirectResponse("/competitors", status_code=303)


# --- Metrics + Team ------------------------------------------------------------


@app.get("/metrics", response_class=HTMLResponse)
def view_metrics(request: Request, db: Session = Depends(get_db), user: User = Depends(require_login)):
    return templates.TemplateResponse(
        "metrics.html", {"request": request, "history": metrics.snapshot_history(db), "user": user}
    )


@app.get("/team", response_class=HTMLResponse)
def view_team(request: Request, db: Session = Depends(get_db), user: User = Depends(require_login)):
    live_scheduler = get_scheduler()
    toggle_states = agent_toggles.states_for(db, [a["key"] for a in AGENT_JOBS])
    agents = []
    for agent in AGENT_JOBS:
        next_run = None
        if agent["job_id"] and live_scheduler is not None:
            job = live_scheduler.get_job(agent["job_id"])
            next_run = job.next_run_time if job else None
        agents.append({**agent, "next_run": next_run, "enabled": toggle_states[agent["key"]]})
    return templates.TemplateResponse("team.html", {"request": request, "agents": agents, "user": user})


@app.post("/team/{key}/toggle")
def toggle_agent(
    key: str,
    enabled: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles()),
):
    agent_toggles.set_enabled(db, key, enabled == "on", updated_by_user_id=user.id)
    return RedirectResponse("/team", status_code=303)


@app.post("/team/{key}/run-now")
def run_agent_now(
    key: str, db: Session = Depends(get_db), user: User = Depends(require_roles())
):
    agent = next((a for a in AGENT_JOBS if a["key"] == key), None)
    if agent is None or not agent["job_id"]:
        raise HTTPException(status_code=404, detail="This agent has no scheduled job to run.")
    job_fn = JOB_FUNCTIONS.get(agent["job_id"])
    if job_fn is None:
        raise HTTPException(status_code=404, detail="No job function registered for this agent.")
    job_fn()
    return RedirectResponse("/team", status_code=303)


# --- Calendly webhook (no auth — Calendly calls this directly) ----------------


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
