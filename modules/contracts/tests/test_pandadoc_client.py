"""Unit tests for pandadoc_client.py — httpx is mocked, no live network."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from modules.contracts.pandadoc_client import PandaDocClient


def _mock_response(json_data, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


def test_create_document_from_template_builds_tokens_and_recipients():
    client = PandaDocClient(api_key="test-key")
    with patch.object(client._client, "post", return_value=_mock_response({"id": "doc_1", "status": "document.uploaded"})) as mock_post:
        result = client.create_document_from_template(
            template_id="tmpl_1",
            name="Contract - Jane",
            filled_data={"price": "5000", "scope": "Website"},
            recipient_email="jane@example.com",
            recipient_first_name="Jane",
            recipient_last_name="Client",
        )

    assert result["id"] == "doc_1"
    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert args[0] == "/documents"
    payload = kwargs["json"]
    assert payload["template_uuid"] == "tmpl_1"
    assert {"name": "price", "value": "5000"} in payload["tokens"]
    assert payload["recipients"][0]["email"] == "jane@example.com"


def test_is_signed_reads_terminal_status():
    client = PandaDocClient(api_key="test-key")
    assert client.is_signed({"status": "document.completed"}) is True
    assert client.is_signed({"status": "document.draft"}) is False


def test_send_document_posts_to_send_endpoint():
    client = PandaDocClient(api_key="test-key")
    with patch.object(client._client, "post", return_value=_mock_response({"id": "doc_1", "status": "document.sent"})) as mock_post:
        client.send_document("doc_1")
    args, _ = mock_post.call_args
    assert args[0] == "/documents/doc_1/send"
