"""Calendly webhook signature verification.

Calendly signs webhook payloads with HMAC-SHA256 in the
`Calendly-Webhook-Signature` header, formatted as `t=<timestamp>,v1=<sig>`.
Docs: https://developer.calendly.com/api-docs/webhook-signatures
"""
from __future__ import annotations

import hashlib
import hmac


def verify_signature(raw_body: bytes, signature_header: str, signing_key: str) -> bool:
    if not signature_header or not signing_key:
        return False
    parts = dict(
        item.split("=", 1) for item in signature_header.split(",") if "=" in item
    )
    timestamp, signature = parts.get("t"), parts.get("v1")
    if not timestamp or not signature:
        return False

    signed_payload = f"{timestamp}.{raw_body.decode('utf-8')}"
    expected = hmac.new(
        signing_key.encode("utf-8"), signed_payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def extract_invitee_email(payload: dict) -> str:
    """Pull the invitee's email out of a Calendly `invitee.created` webhook payload."""
    return (payload.get("payload") or {}).get("email", "")


def extract_event_uri(payload: dict) -> str:
    return (payload.get("payload") or {}).get("event", "") or (payload.get("payload") or {}).get(
        "uri", ""
    )


def extract_scheduled_time(payload: dict) -> str:
    scheduled = (payload.get("payload") or {}).get("scheduled_event") or {}
    return scheduled.get("start_time", "")
