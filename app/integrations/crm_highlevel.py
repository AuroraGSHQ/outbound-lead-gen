"""GoHighLevel (HighLevel) API v2 (a Private Integration / location-level
API key, generated from the client's sub-account under Settings >
Private Integrations).

Uses the upsert endpoint, which HighLevel matches on email/phone within the
given location, so re-syncing the same lead updates the existing contact.
"""
from __future__ import annotations

import httpx

_UPSERT_URL = "https://services.leadconnectorhq.com/contacts/upsert"
_API_VERSION = "2021-07-28"


class CrmError(RuntimeError):
    pass


def upsert_contact(api_key: str, location_id: str, fields: dict) -> dict:
    if not api_key:
        raise CrmError("No GoHighLevel API key configured for this client.")
    if not location_id:
        raise CrmError("No GoHighLevel location ID configured for this client.")

    body = {
        "locationId": location_id,
        "firstName": fields.get("first_name", ""),
        "lastName": fields.get("last_name", ""),
        "email": fields.get("email", ""),
        "phone": fields.get("phone", ""),
        "companyName": fields.get("company", ""),
    }
    body = {k: v for k, v in body.items() if v}
    body["locationId"] = location_id

    resp = httpx.post(
        _UPSERT_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Version": _API_VERSION,
            "Content-Type": "application/json",
        },
        json=body,
        timeout=15,
    )
    if resp.status_code >= 400:
        raise CrmError(f"GoHighLevel returned {resp.status_code}: {resp.text[:300]}")
    return resp.json()
