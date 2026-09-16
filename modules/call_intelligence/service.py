"""Core call-intelligence pipeline: summarize a finished call, extract a
deadline if one was mentioned, and keep nagging about it until it's done.

This is the only file in this module that touches the DB (via
modules/common/db.py::db_conn) or fires notifications (via
modules/common/notifications.py::create_notification) — router.py and
jobs.py both just call into here.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.config import get_settings
from modules.call_intelligence.summarizer import CallSummarizer
from modules.call_intelligence.zoom_client import ZoomClient
from modules.common.db import db_conn
from modules.common.notifications import create_notification

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.utcnow().isoformat(timespec="milliseconds") + "Z"


def _normalize_phone(phone: str) -> str:
    """Strip everything but digits and keep the last 10, so +1 (555) 123-4567,
    555-123-4567 and 15551234567 all compare equal. Good enough for a
    US-centric single-tenant scaffold; not meant to be a full E.164 parser.
    """
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    return digits[-10:] if digits else ""


def _find_contact_by_phone(conn: Connection, phone: str) -> int | None:
    normalized = _normalize_phone(phone)
    if not normalized:
        return None
    rows = conn.execute(text("SELECT id, phone FROM contacts WHERE phone != ''")).mappings().all()
    for row in rows:
        if _normalize_phone(row["phone"]) == normalized:
            return row["id"]
    return None


def _find_contact_by_email(conn: Connection, email: str) -> int | None:
    email = (email or "").strip().lower()
    if not email:
        return None
    row = conn.execute(
        text("SELECT id FROM contacts WHERE lower(email) = :email AND email != ''"),
        {"email": email},
    ).mappings().first()
    return row["id"] if row else None


# ---------------------------------------------------------------------
# process_call
# ---------------------------------------------------------------------
def process_call(call_log_id: int) -> None:
    """Summarize a call_logs row if it has a transcript and hasn't been
    processed yet: writes summary/agreed_items/deadline_mentioned/processed_at,
    fires a `call_summary_ready` notification, and inserts a `deadlines` row
    if a deadline was mentioned. No-op if already processed or no transcript.
    """
    with db_conn() as conn:
        row = conn.execute(
            text(
                """
                SELECT cl.id, cl.transcript, cl.processed_at, cl.contact_id,
                       cl.call_type, c.name AS contact_name, c.company_name
                FROM call_logs cl
                LEFT JOIN contacts c ON c.id = cl.contact_id
                WHERE cl.id = :id
                """
            ),
            {"id": call_log_id},
        ).mappings().first()

    if row is None:
        logger.warning("process_call: no call_logs row with id=%s", call_log_id)
        return
    if row["processed_at"] is not None:
        logger.info("process_call: call_log %s already processed, skipping", call_log_id)
        return
    if not row["transcript"]:
        logger.info("process_call: call_log %s has no transcript yet, skipping", call_log_id)
        return

    settings = get_settings()
    summarizer = CallSummarizer(settings.anthropic_api_key, settings.anthropic_model)
    result = summarizer.summarize(row["transcript"])

    summary = result["summary"]
    agreed_items = result["agreed_items"]
    deadline_mentioned = result["deadline_mentioned"]
    now = _utcnow_iso()

    who = row["contact_name"] or row["company_name"] or f"call #{call_log_id}"

    with db_conn() as conn:
        conn.execute(
            text(
                """
                UPDATE call_logs
                SET summary = :summary,
                    agreed_items = :agreed_items,
                    deadline_mentioned = :deadline_mentioned,
                    processed_at = :processed_at
                WHERE id = :id
                """
            ),
            {
                "summary": summary,
                "agreed_items": json.dumps(agreed_items),
                "deadline_mentioned": deadline_mentioned,
                "processed_at": now,
                "id": call_log_id,
            },
        )

        create_notification(
            "call_summary_ready",
            title=f"Call summary ready — {who}",
            body=summary or "Call processed, but the model returned no summary text.",
            payload={"call_log_id": call_log_id, "call_type": row["call_type"], "agreed_items": agreed_items},
            contact_id=row["contact_id"],
            conn=conn,
        )

        if deadline_mentioned:
            description = (
                agreed_items[0] if agreed_items else (summary or "Deadline mentioned on call")
            )
            conn.execute(
                text(
                    """
                    INSERT INTO deadlines (call_log_id, contact_id, description, due_date)
                    VALUES (:call_log_id, :contact_id, :description, :due_date)
                    """
                ),
                {
                    "call_log_id": call_log_id,
                    "contact_id": row["contact_id"],
                    "description": description,
                    "due_date": deadline_mentioned,
                },
            )

    logger.info("process_call: processed call_log %s (deadline=%s)", call_log_id, deadline_mentioned)


# ---------------------------------------------------------------------
# Zoom ingestion
# ---------------------------------------------------------------------
def create_call_log_from_zoom(meeting_payload: dict[str, Any]) -> int:
    """Given a Zoom `recording.completed` webhook payload (the full event
    body, `{"event": ..., "payload": {"object": {...}}}`, or tolerantly just
    the inner `object` dict), find-or-create the matching call_logs row,
    pull the transcript via ZoomClient, and process it.
    """
    obj = (meeting_payload.get("payload") or {}).get("object")
    if obj is None:
        obj = meeting_payload  # tolerate being handed the inner object directly

    meeting_id = str(obj.get("id") or obj.get("uuid") or "")
    if not meeting_id:
        raise ValueError("Zoom payload missing meeting id/uuid")

    topic = obj.get("topic", "")
    start_time = obj.get("start_time") or None

    candidate_emails = []
    if obj.get("host_email"):
        candidate_emails.append(obj["host_email"])
    for participant in obj.get("participants", []) or []:
        if isinstance(participant, dict) and participant.get("email"):
            candidate_emails.append(participant["email"])

    with db_conn() as conn:
        contact_id = None
        for email in candidate_emails:
            contact_id = _find_contact_by_email(conn, email)
            if contact_id:
                break

        existing = conn.execute(
            text(
                "SELECT id FROM call_logs WHERE call_type = 'zoom' AND external_call_id = :eid"
            ),
            {"eid": meeting_id},
        ).mappings().first()

        if existing:
            call_log_id = existing["id"]
            if contact_id:
                conn.execute(
                    text("UPDATE call_logs SET contact_id = :cid WHERE id = :id AND contact_id IS NULL"),
                    {"cid": contact_id, "id": call_log_id},
                )
        else:
            result = conn.execute(
                text(
                    """
                    INSERT INTO call_logs
                        (contact_id, call_type, direction, external_call_id, outcome, started_at)
                    VALUES
                        (:contact_id, 'zoom', 'outbound', :external_call_id, 'completed', :started_at)
                    """
                ),
                {
                    "contact_id": contact_id,
                    "external_call_id": meeting_id,
                    "started_at": start_time,
                },
            )
            call_log_id = result.lastrowid

    transcript = ""
    zoom = ZoomClient(get_settings())
    try:
        transcript = zoom.get_meeting_transcript(meeting_id)
    except Exception:  # noqa: BLE001 - a Zoom hiccup must not break webhook handling
        logger.exception("create_call_log_from_zoom: failed to pull transcript for meeting %s", meeting_id)
    finally:
        zoom.close()

    if transcript:
        with db_conn() as conn:
            conn.execute(
                text("UPDATE call_logs SET transcript = :t WHERE id = :id"),
                {"t": transcript, "id": call_log_id},
            )
        process_call(call_log_id)
    else:
        logger.info(
            "create_call_log_from_zoom: no transcript available yet for meeting %s (topic=%r)",
            meeting_id,
            topic,
        )

    return call_log_id


# ---------------------------------------------------------------------
# Twilio recorded-line ingestion
# ---------------------------------------------------------------------
def create_call_log_from_twilio_recording(recording_payload: dict[str, Any]) -> int:
    """Given a Twilio recording-status-callback payload (form fields as a
    dict: CallSid, RecordingUrl, From, To, and optionally TranscriptionText
    from legacy <Record transcribe="true">), find-or-create the matching
    call_logs row and process it if a transcript ended up set.
    """
    settings = get_settings()
    call_sid = str(recording_payload.get("CallSid", ""))
    recording_url = str(recording_payload.get("RecordingUrl", ""))
    from_number = str(recording_payload.get("From", ""))
    to_number = str(recording_payload.get("To", ""))
    # TranscriptionText only shows up when the call used legacy
    # <Record transcribe="true"> transcription. Twilio's newer Voice
    # Intelligence product (transcript pulled separately via a Conversational
    # Intelligence API call keyed by the recording/call SID) is NOT wired up
    # here — see the TODO below and the final report.
    transcript = str(recording_payload.get("TranscriptionText", "") or "")

    owner_line = settings.twilio_recorded_line_number
    other_number = to_number if from_number == owner_line else from_number
    direction = "outbound" if from_number == owner_line else "inbound"

    with db_conn() as conn:
        contact_id = _find_contact_by_phone(conn, other_number) if other_number else None

        existing = conn.execute(
            text(
                "SELECT id, transcript FROM call_logs WHERE call_type = 'phone_manual' AND external_call_id = :eid"
            ),
            {"eid": call_sid},
        ).mappings().first()

        if existing:
            call_log_id = existing["id"]
            conn.execute(
                text(
                    """
                    UPDATE call_logs
                    SET recording_url = :recording_url,
                        transcript = CASE WHEN :transcript != '' THEN :transcript ELSE transcript END,
                        contact_id = COALESCE(contact_id, :contact_id)
                    WHERE id = :id
                    """
                ),
                {
                    "recording_url": recording_url,
                    "transcript": transcript,
                    "contact_id": contact_id,
                    "id": call_log_id,
                },
            )
            if not transcript:
                transcript = existing["transcript"]
        else:
            result = conn.execute(
                text(
                    """
                    INSERT INTO call_logs
                        (contact_id, call_type, direction, external_call_id, transcript,
                         recording_url, outcome)
                    VALUES
                        (:contact_id, 'phone_manual', :direction, :external_call_id, :transcript,
                         :recording_url, 'completed')
                    """
                ),
                {
                    "contact_id": contact_id,
                    "direction": direction,
                    "external_call_id": call_sid,
                    "transcript": transcript,
                    "recording_url": recording_url,
                },
            )
            call_log_id = result.lastrowid

    if transcript:
        process_call(call_log_id)
    else:
        # TODO(integration pass): no transcript source wired up for this call.
        # Options for a follow-up: (a) enable Twilio legacy
        # <Record transcribe="true"> on the recorded line's TwiML so
        # TranscriptionText arrives on this same callback, or (b) integrate
        # Twilio Voice Intelligence / Conversational Intelligence (separate
        # API, keyed by RecordingSid) and call process_call() once that
        # transcript lands. For now the row just sits with
        # processed_at IS NULL and recording_url set, waiting to be picked
        # up manually or reprocessed once a transcript exists.
        logger.info(
            "create_call_log_from_twilio_recording: no transcript for CallSid=%s; "
            "recording_url stored, transcript left empty (see TODO in source)",
            call_sid,
        )

    return call_log_id


# ---------------------------------------------------------------------
# Daily deadline sweep
# ---------------------------------------------------------------------
def run_daily_deadline_sweep() -> dict[str, int]:
    """SELECT open, due-or-overdue deadlines and fire a `deadline_due`
    notification for each, incrementing reminded_count.

    Reminder-cap behavior (documented per the README's "your call"): once a
    deadline's reminded_count reaches settings.deadline_max_reminders, we
    STOP firing further notifications for it rather than relabeling forever —
    a deadline nagged 5 times with no action from the owner is either done
    and just not marked completed, or needs a human decision, not more nags.
    The notification for the reminder that reaches the cap is labeled
    "final reminder" so the owner knows nagging is about to stop.
    """
    settings = get_settings()
    max_reminders = max(settings.deadline_max_reminders, 1)
    today = date.today().isoformat()
    now = _utcnow_iso()

    reminded = 0
    capped_skipped = 0

    with db_conn() as conn:
        rows = conn.execute(
            text("SELECT * FROM deadlines WHERE due_date <= :today AND completed = 0"),
            {"today": today},
        ).mappings().all()

        for row in rows:
            if row["reminded_count"] >= max_reminders:
                capped_skipped += 1
                continue

            try:
                days_overdue = (date.today() - date.fromisoformat(row["due_date"])).days
            except ValueError:
                days_overdue = 0
            overdue_phrase = "due today" if days_overdue <= 0 else f"{days_overdue} day(s) overdue"

            is_final = (row["reminded_count"] + 1) >= max_reminders
            label = "Deadline (final reminder)" if is_final else "Deadline due"
            description = row["description"] or "Untitled deadline"

            body = f"{description} — {overdue_phrase} (was due {row['due_date']})."
            if is_final:
                body += " This is the final reminder for this deadline; it won't be nagged again."

            create_notification(
                "deadline_due",
                title=f"{label}: {description}",
                body=body,
                payload={"deadline_id": row["id"], "due_date": row["due_date"], "days_overdue": days_overdue},
                contact_id=row["contact_id"],
                conn=conn,
            )
            conn.execute(
                text(
                    "UPDATE deadlines SET reminded_count = reminded_count + 1, last_reminded_at = :now WHERE id = :id"
                ),
                {"now": now, "id": row["id"]},
            )
            reminded += 1

    logger.info("run_daily_deadline_sweep: reminded=%s capped_skipped=%s", reminded, capped_skipped)
    return {"reminded": reminded, "capped_skipped": capped_skipped}
