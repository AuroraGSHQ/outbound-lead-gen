"""Monday.com GraphQL API (API token — the client generates one from their
avatar > Admin > API, or Profile > Developers on a non-admin seat).

Every board's columns are custom, so there's no fixed field list to push
into — the client's `crm_config['column_map']` maps our field names
(email/phone/company/notes) to that specific board's column IDs, set up
once when connecting the client. Unmapped fields are just skipped rather
than guessed at.
"""
from __future__ import annotations

import json

import httpx

_API_URL = "https://api.monday.com/v2"

_MUTATION = """
mutation ($boardId: ID!, $itemName: String!, $columnValues: JSON!) {
  create_item(board_id: $boardId, item_name: $itemName, column_values: $columnValues) {
    id
  }
}
"""


class CrmError(RuntimeError):
    pass


def create_item(api_key: str, board_id: str, column_map: dict, fields: dict) -> dict:
    if not api_key:
        raise CrmError("No Monday.com API key configured for this client.")
    if not board_id:
        raise CrmError("No Monday.com board ID configured for this client.")

    item_name = fields.get("full_name") or fields.get("company") or fields.get("email") or "New lead"
    column_values = {}
    for our_field, column_id in (column_map or {}).items():
        value = fields.get(our_field)
        if value:
            column_values[column_id] = value

    resp = httpx.post(
        _API_URL,
        headers={"Authorization": api_key, "Content-Type": "application/json"},
        json={
            "query": _MUTATION,
            "variables": {
                "boardId": board_id,
                "itemName": item_name,
                "columnValues": json.dumps(column_values),
            },
        },
        timeout=15,
    )
    if resp.status_code >= 400:
        raise CrmError(f"Monday.com returned {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    if data.get("errors"):
        raise CrmError(f"Monday.com error: {data['errors']}")
    return data
