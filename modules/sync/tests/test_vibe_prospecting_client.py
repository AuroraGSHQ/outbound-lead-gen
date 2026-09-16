"""Tests for modules.sync.vibe_prospecting_client — asserts the exact
request shapes (paths + bodies) documented in the task brief against the
verified Explorium AgentSource API, with httpx mocked (no live network).
"""
from __future__ import annotations

import httpx
import pytest

from modules.sync.vibe_prospecting_client import VibeProspectingClient


def _client_with_mock_transport(handler) -> VibeProspectingClient:
    client = VibeProspectingClient(api_key="test-key")
    # Swap the real httpx.Client's transport for a MockTransport so no
    # network call is made, but base_url/headers still apply.
    client._client = httpx.Client(
        base_url=client._client.base_url,
        headers=client._client.headers,
        transport=httpx.MockTransport(handler),
    )
    return client


def test_match_business_posts_expected_body_and_path():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["api_key_header"] = request.headers.get("api_key")
        captured["body"] = request.content
        return httpx.Response(
            200,
            json={
                "response_context": {},
                "total_results": 1,
                "total_matches": 1,
                "matched_businesses": [
                    {"input": {"name": "Acme Roofing"}, "business_id": "a" * 32}
                ],
            },
        )

    client = _client_with_mock_transport(handler)
    business_id = client.match_business(name="Acme Roofing", domain="acmeroofing.com")

    assert business_id == "a" * 32
    assert captured["method"] == "POST"
    assert captured["url"] == "https://api.explorium.ai/v2/businesses/match"
    assert captured["api_key_header"] == "test-key"

    import json as _json

    body = _json.loads(captured["body"])
    assert body == {
        "businesses_to_match": [{"name": "Acme Roofing", "domain": "acmeroofing.com"}]
    }


def test_match_business_returns_none_on_unmatched():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "response_context": {},
                "total_results": 1,
                "total_matches": 0,
                "matched_businesses": [
                    {"input": {"name": "Nobody Inc"}, "error": "no match", "error_type": "not_found"}
                ],
            },
        )

    client = _client_with_mock_transport(handler)
    assert client.match_business(name="Nobody Inc") is None


def test_match_business_returns_none_with_no_identifying_field():
    client = _client_with_mock_transport(lambda request: httpx.Response(200, json={}))
    assert client.match_business() is None


def test_enrich_firmographics_posts_expected_body_and_path():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["body"] = request.content
        return httpx.Response(
            200,
            json={
                "response_context": {},
                "data": [
                    {
                        "business_id": "a" * 32,
                        "data": {
                            "name": "Acme Roofing",
                            "number_of_employees_range": "11-50",
                            "city_name": "Austin",
                            "region_name": "Texas",
                            "country_name": "United States",
                        },
                    }
                ],
                "total_results": 1,
            },
        )

    client = _client_with_mock_transport(handler)
    result = client.enrich_firmographics(["a" * 32])

    assert captured["method"] == "POST"
    assert captured["url"] == "https://api.explorium.ai/v2/businesses/firmographics/enrich"

    import json as _json

    body = _json.loads(captured["body"])
    assert body == {"business_ids": ["a" * 32]}

    assert result[0]["business_id"] == "a" * 32
    assert result[0]["data"]["name"] == "Acme Roofing"


def test_enrich_firmographics_empty_list_makes_no_request():
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"data": []})

    client = _client_with_mock_transport(handler)
    assert client.enrich_firmographics([]) == []
    assert called is False


def test_requires_api_key():
    with pytest.raises(ValueError):
        VibeProspectingClient(api_key="")
