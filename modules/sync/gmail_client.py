"""Gmail API access for the two watched inboxes (business + personal),
built directly from a refresh token instead of a token file.

Distinct from app/integrations/gmail.py (which loads credentials from a
`GMAIL_TOKEN_PATH` JSON file written by `scripts/gmail_auth.py` — that flow
serves the single outreach-sending inbox). This module authorizes each
inbox separately from the *same* registered OAuth app
(`gmail_oauth_client_id`/`gmail_oauth_client_secret`) plus a
per-inbox refresh token (`gmail_business_refresh_token` /
`gmail_personal_refresh_token`), with no on-disk token file at all.

Kept dumb on purpose: no DB access, no contact-matching, no notification
logic. `modules/sync/service.py` owns all of that.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import google.auth.transport.requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def build_service(client_id: str, client_secret: str, refresh_token: str):
    """Build an authorized Gmail API service object from a bare refresh
    token — no token file, no interactive consent (this always assumes the
    refresh token was already obtained out-of-band during initial OAuth
    setup for that inbox).
    """
    creds = Credentials(
        None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    creds.refresh(google.auth.transport.requests.Request())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def get_own_address(service) -> str:
    """The inbox's own email address, via the profile endpoint — used to
    tell inbound from outbound (is this inbox's address in From or To?).
    """
    profile = service.users().getProfile(userId="me").execute()
    return (profile.get("emailAddress") or "").lower()


def _header(headers: list[dict[str, str]], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


@dataclass
class GmailMessageSummary:
    message_id: str
    thread_id: str
    from_address: str
    to_address: str
    subject: str
    snippet: str
    internal_date: str  # ms since epoch, as a string, per Gmail API


def list_recent_messages(service, since_unix_ts: int, max_results: int = 100) -> list[GmailMessageSummary]:
    """List messages (INBOX + SENT — i.e. any message touching this
    mailbox, not just received ones) newer than `since_unix_ts`, and fetch
    just the metadata this module needs for each: From/To/Subject headers
    plus Gmail's own `snippet` (the `emails` table only stores a snippet,
    not a full body, so there's no need to fetch/parse the full MIME body).
    """
    query = f"after:{int(since_unix_ts)}"
    message_ids: list[str] = []
    page_token: str | None = None
    while True:
        resp = (
            service.users()
            .messages()
            .list(
                userId="me",
                q=query,
                labelIds=["INBOX", "SENT"],
                maxResults=min(max_results - len(message_ids), 500) if max_results else 500,
                pageToken=page_token,
            )
            .execute()
        )
        message_ids.extend(m["id"] for m in resp.get("messages", []) or [])
        page_token = resp.get("nextPageToken")
        if not page_token or (max_results and len(message_ids) >= max_results):
            break

    summaries: list[GmailMessageSummary] = []
    for message_id in message_ids[:max_results] if max_results else message_ids:
        msg = (
            service.users()
            .messages()
            .get(
                userId="me",
                id=message_id,
                format="metadata",
                metadataHeaders=["From", "To", "Subject"],
            )
            .execute()
        )
        headers = msg.get("payload", {}).get("headers", [])
        summaries.append(
            GmailMessageSummary(
                message_id=msg["id"],
                thread_id=msg.get("threadId", ""),
                from_address=_header(headers, "From"),
                to_address=_header(headers, "To"),
                subject=_header(headers, "Subject"),
                snippet=msg.get("snippet", ""),
                internal_date=msg.get("internalDate", ""),
            )
        )
    return summaries
