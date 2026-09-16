"""Vibe Prospecting client — thin wrapper over Explorium's AgentSource API,
used here for re-enrichment (see docs.explorium.ai, fetched live during
this module's build):

- POST /v2/businesses/match          -> business_id for a name/domain
- POST /v2/businesses/firmographics/enrich -> firmographic data for ids

Auth is a plain `api_key` header (not `Authorization: Bearer`). Kept dumb
on purpose: no DB access, no contact-matching logic — that lives in
modules/sync/service.py::run_staleness_sweep.
"""
from __future__ import annotations

from typing import Any

import httpx

VIBE_BASE_URL = "https://api.explorium.ai"


class VibeProspectingClient:
    def __init__(self, api_key: str, timeout: float = 30.0):
        if not api_key:
            raise ValueError("Vibe Prospecting (Explorium) API key is required")
        self._client = httpx.Client(
            base_url=VIBE_BASE_URL,
            timeout=timeout,
            headers={"api_key": api_key, "Content-Type": "application/json"},
        )

    def close(self) -> None:
        self._client.close()

    def match_business(self, *, name: str | None = None, domain: str | None = None) -> str | None:
        """Match a single business by name and/or domain, return its
        `business_id`, or None if unmatched (no identifying field, no
        match, or an error entry in the response).
        """
        if not name and not domain:
            return None

        item: dict[str, Any] = {}
        if name:
            item["name"] = name
        if domain:
            item["domain"] = domain

        payload = {"businesses_to_match": [item]}
        resp = self._client.post("/v2/businesses/match", json=payload)
        resp.raise_for_status()
        data = resp.json()

        matches = data.get("matched_businesses") or []
        if not matches:
            return None
        match = matches[0]
        if match.get("error"):
            return None
        return match.get("business_id")

    def enrich_firmographics(self, business_ids: list[str]) -> list[dict[str, Any]]:
        """Enrich one or more business ids, returning the list of per-id
        `{"business_id": ..., "data": {...}}` entries.
        """
        if not business_ids:
            return []
        payload = {"business_ids": business_ids}
        resp = self._client.post("/v2/businesses/firmographics/enrich", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data.get("data") or []
