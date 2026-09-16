"""CRM auto-maintenance: dual-inbox email logging + lead-matching, and
stale-prospect re-enrichment.

Per modules/README.md's sync-specific note: most of this module's
"reconciliation" (contacts.last_contacted_at / contacts.segment staying
correct after a call or a signed contract) is already handled by SQL
triggers in schema.sql (trg_call_logs_touch_contact,
trg_call_logs_outcome_closed, trg_call_logs_outcome_callback,
trg_contracts_signed_marks_current_client). This module only does the two
things a DB trigger can't: talk to Gmail, and talk to Vibe Prospecting.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses, parseaddr
from typing import Any

from anthropic import Anthropic
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.config import get_settings
from modules.common.db import db_conn
from modules.common.notifications import create_notification
from modules.sync import gmail_client
from modules.sync.vibe_prospecting_client import VibeProspectingClient

logger = logging.getLogger(__name__)

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

# Inbox name -> which Settings field holds its refresh token.
_INBOX_NAMES = ("business", "personal")

_DEFAULT_LOOKBACK_DAYS = 7


def _extract_json(raw: str) -> dict[str, Any]:
    match = _JSON_FENCE_RE.search(raw)
    candidate = match.group(1) if match else raw
    return json.loads(candidate)


# ---------------------------------------------------------------------------
# Contact matching
# ---------------------------------------------------------------------------


def match_contact_by_email(conn: Connection, address: str) -> int | None:
    """Case-insensitive lookup of a contact by email address. Returns None
    for a blank address or no match (an empty `contacts.email` should never
    "match" a blank incoming address).
    """
    address = (address or "").strip().lower()
    if not address:
        return None
    row = conn.execute(
        text(
            """
            SELECT id FROM contacts
            WHERE email != '' AND LOWER(email) = :address
            LIMIT 1
            """
        ),
        {"address": address},
    ).first()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Needs-reply classification (Claude)
# ---------------------------------------------------------------------------


def classify_needs_reply(subject: str, snippet: str) -> bool:
    """A small Claude call: does this inbound email need a reply? Only
    meaningful for inbound messages — callers should skip this (and treat
    needs_reply as False) for outbound ones. Defensive on every front: a
    missing API key, a network error, or unparsable output all just yield
    False rather than blocking the sync.
    """
    settings = get_settings()
    if not settings.anthropic_api_key:
        return False
    try:
        client = Anthropic(api_key=settings.anthropic_api_key)
        resp = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=64,
            system=(
                "You look at one inbound email (subject + snippet) for a small "
                "business owner and decide whether it needs a personal reply "
                "from them (a question, a request, a new inquiry, someone "
                "waiting on an answer) versus something that doesn't (an "
                "automated receipt, a newsletter, a no-reply notification, "
                "spam). Respond with ONLY JSON: "
                '{"needs_reply": true or false}'
            ),
            messages=[
                {
                    "role": "user",
                    "content": f"Subject: {subject}\nSnippet: {snippet}",
                }
            ],
        )
        raw = "".join(block.text for block in resp.content if block.type == "text")
        data = _extract_json(raw)
        return bool(data.get("needs_reply", False))
    except Exception:  # noqa: BLE001 - classification failure must not block sync
        logger.exception("classify_needs_reply failed for subject=%r", subject)
        return False


# ---------------------------------------------------------------------------
# Email sync
# ---------------------------------------------------------------------------


def _since_timestamp(conn: Connection, inbox: str) -> int:
    """Unix timestamp to pass to Gmail's `after:` search operator: the most
    recent `emails.created_at` already stored for this inbox, or a 7-day
    lookback if we've never synced this inbox before. `created_at` is when
    we *stored* the row (not the message's own send time), so this is an
    approximation with deliberate overlap at the boundary — safe, because
    ingestion is deduped by (inbox, gmail_message_id).
    """
    row = conn.execute(
        text("SELECT MAX(created_at) FROM emails WHERE inbox = :inbox"),
        {"inbox": inbox},
    ).first()
    latest = row[0] if row else None
    if latest:
        try:
            dt = datetime.strptime(latest[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            pass
    return int((datetime.now(timezone.utc) - timedelta(days=_DEFAULT_LOOKBACK_DAYS)).timestamp())


def _first_address(header_value: str) -> str:
    _, address = parseaddr(header_value or "")
    return address.lower()


def _all_addresses(header_value: str) -> list[str]:
    return [addr.lower() for _, addr in getaddresses([header_value or ""]) if addr]


def _already_ingested(conn: Connection, inbox: str, gmail_message_id: str) -> bool:
    row = conn.execute(
        text(
            "SELECT id FROM emails WHERE inbox = :inbox AND gmail_message_id = :mid LIMIT 1"
        ),
        {"inbox": inbox, "mid": gmail_message_id},
    ).first()
    return row is not None


def _ingest_message(
    conn: Connection, inbox: str, own_address: str, msg: "gmail_client.GmailMessageSummary"
) -> dict[str, int]:
    counts = {"new_rows": 0, "flagged": 0, "needs_reply": 0, "notifications_fired": 0}

    if not msg.message_id or _already_ingested(conn, inbox, msg.message_id):
        return counts

    from_address = _first_address(msg.from_address)
    to_addresses = _all_addresses(msg.to_address)

    if from_address == own_address:
        direction = "outbound"
        other_address = to_addresses[0] if to_addresses else ""
    elif own_address in to_addresses:
        direction = "inbound"
        other_address = from_address
    else:
        # Ambiguous (e.g. own address only on Bcc/Cc) — default to inbound
        # and match on the sender, the safer assumption for "don't drop it".
        direction = "inbound"
        other_address = from_address

    contact_id = match_contact_by_email(conn, other_address)
    flagged = contact_id is None

    needs_reply = False
    if direction == "inbound":
        needs_reply = classify_needs_reply(msg.subject, msg.snippet)

    should_notify = flagged or needs_reply

    conn.execute(
        text(
            """
            INSERT INTO emails (
                contact_id, inbox, direction, gmail_message_id, gmail_thread_id,
                from_address, to_address, subject, snippet, flagged, needs_reply, notified
            ) VALUES (
                :contact_id, :inbox, :direction, :gmail_message_id, :gmail_thread_id,
                :from_address, :to_address, :subject, :snippet, :flagged, :needs_reply, :notified
            )
            """
        ),
        {
            "contact_id": contact_id,
            "inbox": inbox,
            "direction": direction,
            "gmail_message_id": msg.message_id,
            "gmail_thread_id": msg.thread_id,
            "from_address": msg.from_address,
            "to_address": msg.to_address,
            "subject": msg.subject,
            "snippet": msg.snippet,
            "flagged": 1 if flagged else 0,
            "needs_reply": 1 if needs_reply else 0,
            "notified": 1 if should_notify else 0,
        },
    )

    counts["new_rows"] = 1
    counts["flagged"] = 1 if flagged else 0
    counts["needs_reply"] = 1 if needs_reply else 0

    if should_notify:
        reason = "an unmatched sender" if flagged else "a reply is likely needed"
        create_notification(
            "missed_message",
            title=f"[{inbox}] {msg.subject or '(no subject)'}",
            body=f"From {msg.from_address} — {reason}. Snippet: {msg.snippet}",
            payload={
                "inbox": inbox,
                "gmail_message_id": msg.message_id,
                "gmail_thread_id": msg.thread_id,
                "flagged": flagged,
                "needs_reply": needs_reply,
            },
            contact_id=contact_id,
            conn=conn,
        )
        counts["notifications_fired"] = 1

    return counts


def sync_emails() -> dict[str, int]:
    """Poll both configured inboxes for messages since the last sync, log
    each new one to `emails`, match a contact, classify inbound
    needs-reply, and fire a `missed_message` notification for anything
    flagged or needing a reply (once per row, via `emails.notified`).
    Skips an inbox entirely if its refresh token isn't configured.
    """
    settings = get_settings()
    result = {
        "business_seen": 0,
        "personal_seen": 0,
        "new_rows": 0,
        "flagged": 0,
        "needs_reply": 0,
        "notifications_fired": 0,
    }

    inbox_tokens = {
        "business": settings.gmail_business_refresh_token,
        "personal": settings.gmail_personal_refresh_token,
    }

    for inbox in _INBOX_NAMES:
        refresh_token = inbox_tokens[inbox]
        if not refresh_token:
            logger.info("Skipping %s inbox sync — no refresh token configured", inbox)
            continue

        try:
            service = gmail_client.build_service(
                settings.gmail_oauth_client_id, settings.gmail_oauth_client_secret, refresh_token
            )
            own_address = gmail_client.get_own_address(service)
        except Exception:  # noqa: BLE001 - one inbox's auth failure shouldn't block the other
            logger.exception("Failed to build Gmail service for %s inbox", inbox)
            continue

        with db_conn() as conn:
            since_ts = _since_timestamp(conn, inbox)

        try:
            messages = gmail_client.list_recent_messages(service, since_ts)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to list messages for %s inbox", inbox)
            continue

        result[f"{inbox}_seen"] = len(messages)

        for msg in messages:
            with db_conn() as conn:
                counts = _ingest_message(conn, inbox, own_address, msg)
            result["new_rows"] += counts["new_rows"]
            result["flagged"] += counts["flagged"]
            result["needs_reply"] += counts["needs_reply"]
            result["notifications_fired"] += counts["notifications_fired"]

    return result


# ---------------------------------------------------------------------------
# Staleness / re-enrichment sweep
# ---------------------------------------------------------------------------


def _guess_domain(email_address: str) -> str | None:
    """`contacts` has no dedicated domain column, so for the "name" match
    fall back to guessing a domain off the contact's own email address —
    cheap and often right for a small local business where the owner's
    email domain *is* the business domain.
    """
    if not email_address or "@" not in email_address:
        return None
    domain = email_address.split("@", 1)[1].strip().lower()
    return domain or None


def _fetch_stale_prospects(conn: Connection, stale_days: int) -> list[dict[str, Any]]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=stale_days)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )
    rows = conn.execute(
        text(
            """
            SELECT id, name, email, company_name, notes
            FROM contacts
            WHERE segment = 'prospect'
              AND (last_contacted_at IS NULL OR last_contacted_at < :cutoff)
            """
        ),
        {"cutoff": cutoff},
    ).mappings()
    return [dict(row) for row in rows]


def _fetch_missing_fields_contacts(conn: Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            """
            SELECT id, name, email, company_name, notes
            FROM contacts
            WHERE company_name = ''
            """
        )
    ).mappings()
    return [dict(row) for row in rows]


def _enrich_contact(client: VibeProspectingClient, contact: dict[str, Any], result: dict[str, int]) -> None:
    name = contact.get("company_name") or None
    domain = _guess_domain(contact.get("email", ""))

    try:
        business_id = client.match_business(name=name, domain=domain)
    except Exception:  # noqa: BLE001 - one contact's API failure shouldn't stop the sweep
        logger.exception("Vibe Prospecting match_business failed for contact id=%s", contact.get("id"))
        result["unmatched"] += 1
        return

    if not business_id:
        result["unmatched"] += 1
        return

    result["matched"] += 1

    try:
        enriched = client.enrich_firmographics([business_id])
    except Exception:  # noqa: BLE001
        logger.exception(
            "Vibe Prospecting enrich_firmographics failed for contact id=%s", contact.get("id")
        )
        return

    if not enriched:
        return

    data = enriched[0].get("data") or {}
    industry = (
        data.get("linkedin_industry_category")
        or data.get("naics_description")
        or data.get("sic_code_description")
        or ""
    )
    employees = data.get("number_of_employees_range") or ""
    location = ", ".join(
        filter(None, [data.get("city_name"), data.get("region_name"), data.get("country_name")])
    )
    found_name = data.get("name") or ""

    # No dedicated industry/location/employee-count columns on `contacts`
    # (just company_name + free-text notes) — record what re-enrichment
    # found as a dated line appended to notes. See modules/sync/README.md
    # vs schema.sql: a reasonable reading given the schema's actual shape,
    # flagged for the integration pass.
    note_line = (
        f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d')} sync] "
        f"industry={industry or 'unknown'}, employees={employees or 'unknown'}, "
        f"location={location or 'unknown'}"
    )

    set_clauses = [
        "notes = CASE WHEN notes = '' THEN :note_line ELSE notes || char(10) || :note_line END",
        "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')",
    ]
    params: dict[str, Any] = {"id": contact["id"], "note_line": note_line}
    if not contact.get("company_name") and found_name:
        set_clauses.append("company_name = :company_name")
        params["company_name"] = found_name

    with db_conn() as conn:
        conn.execute(
            text(f"UPDATE contacts SET {', '.join(set_clauses)} WHERE id = :id"),
            params,
        )

    result["enriched"] += 1


def run_staleness_sweep() -> dict[str, int]:
    """Weekly sweep: re-enrich prospects untouched for
    `settings.sync_stale_prospect_days`+ days, and any contact still
    missing `company_name`, via Vibe Prospecting. Skips entirely (with a
    single warning, not a per-contact error) if the API key isn't
    configured.

    Note: the README's "first enrichment attempt on creation" isn't
    something this module can hook — nothing calls into `sync` when
    another module inserts a `contacts` row (no cross-module imports, and
    a DB trigger can't make an HTTP call). This weekly sweep is the only
    enrichment pass that actually runs.
    """
    settings = get_settings()
    result = {
        "stale_prospects_checked": 0,
        "missing_fields_checked": 0,
        "matched": 0,
        "enriched": 0,
        "unmatched": 0,
    }

    if not settings.vibe_prospecting_api_key:
        logger.warning(
            "vibe_prospecting_api_key not configured — skipping staleness/re-enrichment sweep"
        )
        return result

    with db_conn() as conn:
        stale_rows = _fetch_stale_prospects(conn, settings.sync_stale_prospect_days)
        missing_rows = _fetch_missing_fields_contacts(conn)

    result["stale_prospects_checked"] = len(stale_rows)
    result["missing_fields_checked"] = len(missing_rows)

    client = VibeProspectingClient(settings.vibe_prospecting_api_key)
    try:
        seen_ids: set[int] = set()
        for contact in stale_rows + missing_rows:
            if contact["id"] in seen_ids:
                continue
            seen_ids.add(contact["id"])
            _enrich_contact(client, contact, result)
    finally:
        client.close()

    return result
