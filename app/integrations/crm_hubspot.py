"""HubSpot Contacts API (private app access token — generate one from the
client's HubSpot account under Settings > Integrations > Private Apps, with
the `crm.objects.contacts.write` scope).

Uses the batch upsert endpoint keyed on email, so re-syncing the same lead
updates the existing contact instead of creating a duplicate.
"""
from __future__ import annotations

import httpx

_UPSERT_URL = "https://api.hubapi.com/crm/v3/objects/contacts/batch/upsert"


class CrmError(RuntimeError):
    pass


def upsert_contact(api_key: str, fields: dict) -> dict:
    if not api_key:
        raise CrmError("No HubSpot API key configured for this client.")
    email = (fields.get("email") or "").strip()
    if not email:
        raise CrmError("HubSpot upsert requires an email address.")

    properties = {
        "email": email,
        "firstname": fields.get("first_name", ""),
        "lastname": fields.get("last_name", ""),
        "phone": fields.get("phone", ""),
        "company": fields.get("company", ""),
    }
    properties = {k: v for k, v in properties.items() if v}

    resp = httpx.post(
        _UPSERT_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"inputs": [{"idProperty": "email", "id": email, "properties": properties}]},
        timeout=15,
    )
    if resp.status_code >= 400:
        raise CrmError(f"HubSpot returned {resp.status_code}: {resp.text[:300]}")
    return resp.json()
