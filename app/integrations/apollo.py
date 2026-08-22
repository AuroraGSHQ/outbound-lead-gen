"""Apollo.io client for prospecting.

Apollo's "mixed people search" endpoint (POST /v1/mixed_people/search) lets
you filter by organization industry/headcount/location and person title in
one call, which is exactly the ICP filter shape we need. Docs:
https://docs.apollo.io/docs/find-people-using-filters

Two things worth knowing if you extend this:
- `organization_locations` filters by the *company's* HQ location, which is
  what matters for a local trade business (you don't care where an
  individual contact happens to live, you care where the business you'd
  service is). We use that, not `person_locations`.
- Industry filtering has two modes: `organization_industry_tag_ids` wants
  Apollo's own opaque tag IDs (looked up via a separate picklist endpoint —
  not worth the extra round trip here), while `q_organization_keyword_tags`
  takes plain free-text keywords ("general contractor", "property
  management", ...) and matches against them directly. We use the latter so
  config/icp.yaml can just list human-readable industries/keywords.

We keep this client dumb (search in, plain dicts out) — all
scoring/deduping/decision logic lives in app/icp.py and app/services/sourcing.py.
"""
from __future__ import annotations

from typing import Any

import httpx

APOLLO_BASE_URL = "https://api.apollo.io/v1"


class ApolloClient:
    def __init__(self, api_key: str, timeout: float = 30.0):
        if not api_key:
            raise ValueError("Apollo API key is required")
        self._api_key = api_key
        # Apollo requires the key as a request header, not a body/query param —
        # sending it as "api_key" in the JSON body (an older pattern still
        # shown in some third-party docs) gets rejected with 422
        # INVALID_API_KEY_LOCATION. See https://docs.apollo.io/docs/test-api-key
        self._client = httpx.Client(
            base_url=APOLLO_BASE_URL,
            timeout=timeout,
            headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
        )

    def close(self) -> None:
        self._client.close()

    def search_people(
        self,
        *,
        industries: list[str] | None = None,
        titles: list[str] | None = None,
        locations: list[str] | None = None,
        company_size_min: int | None = None,
        company_size_max: int | None = None,
        page: int = 1,
        per_page: int = 25,
    ) -> list[dict[str, Any]]:
        """Search for people matching ICP filters, return normalized candidate dicts.

        `locations` filters by the target company's HQ location (city/state/
        region strings like "Austin, Texas") — for a local service business
        this is the filter that actually keeps leads inside your service
        area, so make sure config/icp.yaml's target_locations is set before
        running this for real.
        """
        payload: dict[str, Any] = {
            "page": page,
            "per_page": per_page,
        }
        if titles:
            payload["person_titles"] = titles
        if locations:
            payload["organization_locations"] = locations
        if industries:
            payload["q_organization_keyword_tags"] = industries
        if company_size_min is not None or company_size_max is not None:
            lo = company_size_min or 1
            hi = company_size_max or 100000
            payload["organization_num_employees_ranges"] = [f"{lo},{hi}"]

        resp = self._client.post("/mixed_people/search", json=payload)
        resp.raise_for_status()
        data = resp.json()

        candidates: list[dict[str, Any]] = []
        for person in data.get("people", []):
            org = person.get("organization") or {}
            email = person.get("email") or person.get("personal_email") or ""
            if not email:
                continue  # no way to reach them, skip
            candidates.append(
                {
                    "company_name": org.get("name", ""),
                    "domain": (org.get("primary_domain") or org.get("website_url") or "")
                    .replace("https://", "")
                    .replace("http://", "")
                    .rstrip("/"),
                    "industry": org.get("industry", ""),
                    "company_size": org.get("estimated_num_employees"),
                    "location": ", ".join(
                        filter(None, [person.get("city"), person.get("state"), person.get("country")])
                    ),
                    "contact_name": person.get("name", ""),
                    "contact_title": person.get("title", ""),
                    "contact_email": email,
                    "linkedin_url": person.get("linkedin_url", ""),
                    "description": org.get("short_description", ""),
                    "source": "apollo",
                }
            )
        return candidates
