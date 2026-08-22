"""Gmail send + poll, via the Gmail API (OAuth2 user consent).

Setup is a one-time `python scripts/gmail_auth.py` (see docs/SETUP.md) which
writes a refresh token to GMAIL_TOKEN_PATH. Everything here just uses that
token; it never runs the OAuth flow itself (so it works headless on a server).
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]


def load_credentials(token_path: str) -> Credentials:
    path = Path(token_path)
    if not path.exists():
        raise FileNotFoundError(
            f"No Gmail token at {token_path}. Run `python scripts/gmail_auth.py` first."
        )
    creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        path.write_text(creds.to_json())
    return creds


def build_service(token_path: str):
    creds = load_credentials(token_path)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


@dataclass
class SentEmail:
    message_id: str
    thread_id: str


def send_email(
    service,
    *,
    sender: str,
    to: str,
    subject: str,
    body: str,
    thread_id: str | None = None,
    in_reply_to: str | None = None,
) -> SentEmail:
    mime = MIMEText(body)
    mime["to"] = to
    mime["from"] = sender
    mime["subject"] = subject
    if in_reply_to:
        mime["In-Reply-To"] = in_reply_to
        mime["References"] = in_reply_to

    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
    payload: dict[str, Any] = {"raw": raw}
    if thread_id:
        payload["threadId"] = thread_id

    result = service.users().messages().send(userId="me", body=payload).execute()
    return SentEmail(message_id=result["id"], thread_id=result["threadId"])


def _header(headers: list[dict[str, str]], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _extract_body_text(payload: dict[str, Any]) -> str:
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
    for part in payload.get("parts", []) or []:
        text = _extract_body_text(part)
        if text:
            return text
    return ""


def get_message(service, message_id: str) -> dict[str, Any]:
    msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    headers = msg.get("payload", {}).get("headers", [])
    return {
        "id": msg["id"],
        "thread_id": msg["threadId"],
        "from": _header(headers, "From"),
        "subject": _header(headers, "Subject"),
        "body": _extract_body_text(msg.get("payload", {})),
        "internal_date": msg.get("internalDate"),
    }


def list_message_ids_in_thread(service, thread_id: str) -> list[str]:
    thread = service.users().threads().get(userId="me", id=thread_id, format="minimal").execute()
    return [m["id"] for m in thread.get("messages", [])]
