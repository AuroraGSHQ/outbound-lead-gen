"""Segment scripts + the dial-window run loop.

`run_dial_window(settings)` is the entry point the scheduled job calls. It's
intentionally synchronous/sequential (dialer_max_concurrency caps how many
calls we *place* per run, not literal thread concurrency — the voice agent
provider handles the actual concurrent call legs on their side).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from modules.common.db import db_conn
from modules.dialer.client import VoiceAgentClient, VoiceAgentError

logger = logging.getLogger(__name__)

# Outcomes that count as "didn't reach them" for retry-spacing/backoff purposes.
NO_ANSWER_OUTCOMES = {"no_answer", "busy", "failed", "voicemail"}

# Outcomes that count as "we actually reached/spoke to them" and reset retry counting.
REAL_CONTACT_OUTCOMES = {
    "completed",
    "closed",
    "in_progress",
    "callback_requested",
    "not_interested",
    "do_not_call_requested",
}


def _now_utc() -> datetime:
    return datetime.utcnow()


def _local_now(settings: Any) -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(settings.dialer_timezone))
    except Exception:
        logger.warning(
            "Could not load timezone %r, falling back to naive UTC for window check",
            settings.dialer_timezone,
        )
        return datetime.utcnow()


def in_dial_window(settings: Any, now: datetime | None = None) -> bool:
    """True if `now` (local to dialer_timezone) falls within
    [dialer_window_start_hour, dialer_window_end_hour)."""
    local_now = now if now is not None else _local_now(settings)
    return settings.dialer_window_start_hour <= local_now.hour < settings.dialer_window_end_hour


# ---------------------------------------------------------------------------
# Segment script / objective builders
# ---------------------------------------------------------------------------


def _service_line_framing(service_line: str) -> str:
    lines = [s.strip() for s in (service_line or "").split(",") if s.strip()]
    if not lines:
        return "our services"
    labels = {"marketing": "marketing services", "junk_removal": "junk removal services"}
    named = [labels.get(line, line) for line in lines]
    if len(named) == 1:
        return named[0]
    return " and ".join(named)


def build_prospect_script(contact: dict[str, Any]) -> str:
    framing = _service_line_framing(contact.get("service_line", ""))
    company = contact.get("company_name") or "their business"
    return (
        f"You are calling {contact.get('name') or 'the contact'} at {company}, a cold "
        f"prospect who has not talked to us before. Objective: quick discovery — confirm "
        f"you have the right person, ask 1-2 open questions about how they currently "
        f"handle {framing}, and see if there's a fit. If there's interest, pitch how we "
        f"help with {framing} and try to book a follow-up call or meeting. Be brief, "
        f"friendly, and respect their time. If they ask not to be called again, "
        f"acknowledge it immediately and end the call politely."
    )


def build_current_client_script(contact: dict[str, Any]) -> str:
    company = contact.get("company_name") or "their business"
    framing = _service_line_framing(contact.get("service_line", ""))
    return (
        f"You are calling {contact.get('name') or 'the contact'} at {company}, an "
        f"existing/current client. Objective: a friendly check-in — ask how things are "
        f"going with {framing}, surface any issues early, and look for a natural upsell "
        f"or expansion opportunity if things are going well. This is a relationship call, "
        f"not a hard sell."
    )


def build_past_client_script(contact: dict[str, Any]) -> str:
    company = contact.get("company_name") or "their business"
    framing = _service_line_framing(contact.get("service_line", ""))
    return (
        f"You are calling {contact.get('name') or 'the contact'} at {company}, a past "
        f"client we haven't worked with in a while. Objective: re-engagement — warmly "
        f"reconnect, ask what's changed for their business, and gauge interest in "
        f"restarting {framing}. Acknowledge it's been a while without being awkward "
        f"about it."
    )


def build_callback_requested_script(contact: dict[str, Any], last_summary: str) -> str:
    company = contact.get("company_name") or "their business"
    ask = last_summary.strip() if last_summary and last_summary.strip() else (
        "no specific ask was recorded from the last call — ask them what they'd "
        "like to follow up on"
    )
    return (
        f"You are calling {contact.get('name') or 'the contact'} at {company} back "
        f"because they asked for a callback. Objective: follow up specifically on this: "
        f"\"{ask}\". Don't restart the pitch from scratch — pick up where the last "
        f"conversation left off."
    )


def _fetch_last_call_summary(conn: Connection, contact_id: int) -> str:
    row = conn.execute(
        text(
            """
            SELECT summary FROM call_logs
            WHERE contact_id = :contact_id AND summary != ''
            ORDER BY created_at DESC LIMIT 1
            """
        ),
        {"contact_id": contact_id},
    ).fetchone()
    return row[0] if row else ""


def build_script_for_contact(conn: Connection, contact: dict[str, Any]) -> str:
    """Dispatch to the right segment script builder for this contact."""
    segment = contact.get("segment")
    if segment == "prospect":
        return build_prospect_script(contact)
    if segment == "current_client":
        return build_current_client_script(contact)
    if segment == "past_client":
        return build_past_client_script(contact)
    if segment == "callback_requested":
        last_summary = _fetch_last_call_summary(conn, contact["id"])
        return build_callback_requested_script(contact, last_summary)
    # Unknown/future segment: fall back to a neutral prospect-style script.
    return build_prospect_script(contact)


# ---------------------------------------------------------------------------
# Eligibility / retry gating
# ---------------------------------------------------------------------------


def _fetch_eligible_contacts(conn: Connection, settings: Any, now: datetime) -> list[dict[str, Any]]:
    cooldown_cutoff = (now - timedelta(hours=settings.dialer_cooldown_hours)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )
    rows = conn.execute(
        text(
            """
            SELECT id, lead_id, name, phone, email, company_name, segment,
                   service_line, do_not_call, last_contacted_at, notes
            FROM contacts
            WHERE do_not_call = 0
              AND phone != ''
              AND (last_contacted_at IS NULL OR last_contacted_at < :cooldown_cutoff)
            ORDER BY (last_contacted_at IS NULL) DESC, last_contacted_at ASC
            LIMIT :limit
            """
        ),
        {"cooldown_cutoff": cooldown_cutoff, "limit": settings.dialer_max_concurrency},
    ).mappings()
    return [dict(row) for row in rows]


def _recent_attempts_since_last_contact(conn: Connection, contact_id: int) -> list[dict[str, Any]]:
    """All call_logs attempts for this contact since (and including) their
    most recent "real contact" outcome, most recent first. Used to count
    consecutive no-answer-type attempts and find the most recent attempt time.
    """
    rows = conn.execute(
        text(
            """
            SELECT id, outcome, created_at, started_at
            FROM call_logs
            WHERE contact_id = :contact_id AND call_type = 'ai_outbound'
            ORDER BY created_at DESC
            """
        ),
        {"contact_id": contact_id},
    ).mappings()

    attempts: list[dict[str, Any]] = []
    for row in rows:
        attempts.append(dict(row))
        if row["outcome"] in REAL_CONTACT_OUTCOMES:
            break  # stop counting past the last time we actually reached them
    return attempts


def retry_gate(
    conn: Connection,
    settings: Any,
    contact_id: int,
    now: datetime,
) -> tuple[bool, str]:
    """Returns (allowed, reason). reason is only meaningful when not allowed."""
    attempts = _recent_attempts_since_last_contact(conn, contact_id)
    if not attempts:
        return True, ""

    # If the most recent attempt was a real contact (not a no-answer type),
    # nothing to gate here beyond the cooldown already applied in the query.
    most_recent = attempts[0]
    if most_recent["outcome"] not in NO_ANSWER_OUTCOMES and most_recent["outcome"] != "in_progress":
        return True, ""

    no_answer_count = sum(1 for a in attempts if a["outcome"] in NO_ANSWER_OUTCOMES)
    if no_answer_count >= settings.dialer_max_retries:
        return False, f"max_retries_reached ({no_answer_count}/{settings.dialer_max_retries})"

    last_attempt_time_raw = most_recent.get("started_at") or most_recent.get("created_at")
    if last_attempt_time_raw:
        last_attempt_time = _parse_iso(last_attempt_time_raw)
        if last_attempt_time is not None:
            spacing = timedelta(minutes=settings.dialer_retry_spacing_minutes)
            if now - last_attempt_time < spacing:
                return False, "retry_spacing_not_elapsed"

    return True, ""


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _refetch_do_not_call(conn: Connection, contact_id: int) -> bool:
    """Compliance-critical: re-read do_not_call fresh, right before dialing —
    not from the batch queried earlier in the run. Returns True if the
    contact must NOT be called."""
    row = conn.execute(
        text("SELECT do_not_call FROM contacts WHERE id = :id"),
        {"id": contact_id},
    ).fetchone()
    if row is None:
        return True  # contact vanished/was deleted mid-run; don't call
    return bool(row[0])


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_dial_window(settings: Any) -> dict[str, Any]:
    """Called by the scheduled job. Computes the current local time, no-ops
    outside the configured dial window, otherwise queries eligible contacts,
    applies retry gating, re-checks do_not_call immediately before each call,
    places the call, and logs it. Returns a summary dict.
    """
    local_now = _local_now(settings)
    if not in_dial_window(settings, local_now):
        return {
            "ran": False,
            "reason": "outside_dial_window",
            "local_hour": local_now.hour,
            "window": [settings.dialer_window_start_hour, settings.dialer_window_end_hour],
        }

    now = _now_utc()
    summary: dict[str, Any] = {
        "ran": True,
        "attempted": 0,
        "placed": 0,
        "skipped_do_not_call": 0,
        "skipped_retry_gate": 0,
        "errors": 0,
        "by_segment": {},
        "by_outcome": {},
    }

    with db_conn() as conn:
        contacts = _fetch_eligible_contacts(conn, settings, now)

    voice_client: VoiceAgentClient | None = None
    try:
        for contact in contacts:
            summary["attempted"] += 1
            segment = contact.get("segment", "unknown")
            summary["by_segment"].setdefault(segment, 0)

            with db_conn() as conn:
                allowed, reason = retry_gate(conn, settings, contact["id"], now)
                if not allowed:
                    summary["skipped_retry_gate"] += 1
                    logger.info(
                        "Dialer: skipping contact %s (retry gate: %s)", contact["id"], reason
                    )
                    continue

                # Compliance-critical: re-check do_not_call immediately before
                # dialing, fresh from the DB, not from the earlier batch query.
                if _refetch_do_not_call(conn, contact["id"]):
                    summary["skipped_do_not_call"] += 1
                    logger.info(
                        "Dialer: skipping contact %s — do_not_call flipped true mid-run",
                        contact["id"],
                    )
                    continue

                script = build_script_for_contact(conn, contact)

            if voice_client is None:
                voice_client = VoiceAgentClient(settings)

            context = {
                "segment": segment,
                "objective": script,
                "contact_name": contact.get("name", ""),
                "company_name": contact.get("company_name", ""),
                "service_line": contact.get("service_line", ""),
            }

            try:
                result = voice_client.place_call(to_number=contact["phone"], context=context)
                outcome = result.get("status") or "in_progress"
                external_call_id = result.get("external_call_id", "")
            except VoiceAgentError:
                logger.exception("Dialer: call placement failed for contact %s", contact["id"])
                summary["errors"] += 1
                outcome = "call_placement_failed"
                external_call_id = ""

            with db_conn() as conn:
                conn.execute(
                    text(
                        """
                        INSERT INTO call_logs
                            (contact_id, call_type, direction, external_call_id,
                             outcome, started_at)
                        VALUES
                            (:contact_id, 'ai_outbound', 'outbound', :external_call_id,
                             :outcome, :started_at)
                        """
                    ),
                    {
                        "contact_id": contact["id"],
                        "external_call_id": external_call_id,
                        "outcome": outcome,
                        "started_at": now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    },
                )
                conn.execute(
                    text("UPDATE contacts SET last_contacted_at = :now, updated_at = :now WHERE id = :id"),
                    {"now": now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "id": contact["id"]},
                )

            if outcome != "call_placement_failed":
                summary["placed"] += 1
            summary["by_segment"][segment] += 1
            summary["by_outcome"][outcome] = summary["by_outcome"].get(outcome, 0) + 1
    finally:
        if voice_client is not None:
            voice_client.close()

    return summary
