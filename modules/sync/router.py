"""Sync HTTP surface: read the flagged/needs-reply email inbox, and manually
trigger the two sweeps (they're normally cron-driven — see jobs.py — but a
manual trigger is handy while wiring/testing Gmail/Vibe Prospecting creds).

Mounted centrally into app/main.py via app.include_router(router).
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import text

from app.config import get_settings
from modules.common.db import db_conn
from modules.sync.service import run_staleness_sweep, sync_emails

router = APIRouter(prefix="/sync", tags=["sync"])

_security = HTTPBasic()


def require_owner(credentials: HTTPBasicCredentials = Depends(_security)) -> str:
    settings = get_settings()
    correct_user = secrets.compare_digest(credentials.username, settings.owner_ui_username)
    correct_pass = secrets.compare_digest(credentials.password, settings.owner_ui_password)
    if not (correct_user and correct_pass):
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"})
    return credentials.username


@router.get("/emails/flagged")
def flagged_emails(limit: int = 100, _owner: str = Depends(require_owner)):
    with db_conn() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, contact_id, inbox, direction, gmail_message_id, gmail_thread_id,
                       from_address, to_address, subject, snippet, flagged, needs_reply,
                       notified, created_at
                FROM emails
                WHERE flagged = 1 OR needs_reply = 1
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            {"limit": limit},
        ).mappings()
        emails = [dict(row) for row in rows]
    return {"emails": emails}


@router.post("/emails/run")
def run_email_sync(_owner: str = Depends(require_owner)):
    return sync_emails()


@router.post("/staleness/run")
def run_staleness(_owner: str = Depends(require_owner)):
    return run_staleness_sweep()
