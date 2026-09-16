"""PandaDoc API client.

Thin httpx wrapper — dumb in/out, no business logic, no DB access. Mirrors
the style of app/integrations/apollo.py: a small class that takes its API
key, exposes a couple of methods, and lets `raise_for_status()` surface
errors instead of swallowing them.

Docs: https://developers.pandadoc.com/reference/about
- Create document from template: POST /public/v1/documents
- Get document (status/details): GET /public/v1/documents/{id}
- Send document for signature: POST /public/v1/documents/{id}/send

Auth: header `Authorization: API-Key {key}` (PandaDoc's own scheme, not
Bearer).
"""
from __future__ import annotations

from typing import Any

import httpx

PANDADOC_BASE_URL = "https://api.pandadoc.com/public/v1"


class PandaDocClient:
    def __init__(self, api_key: str, timeout: float = 30.0):
        if not api_key:
            raise ValueError("PandaDoc API key is required")
        self._client = httpx.Client(
            base_url=PANDADOC_BASE_URL,
            timeout=timeout,
            headers={
                "Authorization": f"API-Key {api_key}",
                "Content-Type": "application/json",
            },
        )

    def close(self) -> None:
        self._client.close()

    def create_document_from_template(
        self,
        *,
        template_id: str,
        name: str,
        filled_data: dict[str, Any],
        recipient_email: str,
        recipient_first_name: str = "",
        recipient_last_name: str = "",
    ) -> dict[str, Any]:
        """Create a filled-but-unsent document from a template.

        `filled_data` (flat dict of merge-field name -> value) is translated
        into PandaDoc's `tokens` array. The document is created in
        `document.uploaded`/`document.draft`-equivalent state — PandaDoc
        does not send anything until `send_document` is called separately,
        which is what keeps this a "fill, don't send" step.
        """
        tokens = [{"name": str(key), "value": "" if value is None else str(value)} for key, value in filled_data.items()]
        payload: dict[str, Any] = {
            "name": name,
            "template_uuid": template_id,
            "tokens": tokens,
            "recipients": [
                {
                    "email": recipient_email,
                    "first_name": recipient_first_name,
                    "last_name": recipient_last_name,
                    "role": "Client",
                }
            ],
        }
        resp = self._client.post("/documents", json=payload)
        resp.raise_for_status()
        return resp.json()

    def get_document(self, document_id: str) -> dict[str, Any]:
        """Fetch a document's current status/details. Useful both for the
        create-completion poll (PandaDoc creates documents asynchronously —
        status starts as `document.uploaded` and becomes `document.draft`
        once ready to send) and for the safety-net signed-status poll.
        """
        resp = self._client.get(f"/documents/{document_id}")
        resp.raise_for_status()
        return resp.json()

    def send_document(
        self,
        document_id: str,
        *,
        subject: str = "Please sign your contract",
        message: str = "Please review and sign the attached contract.",
    ) -> dict[str, Any]:
        """Send a previously-created document out for signature."""
        payload = {"message": message, "subject": subject, "silent": False}
        resp = self._client.post(f"/documents/{document_id}/send", json=payload)
        resp.raise_for_status()
        return resp.json()

    def is_signed(self, document_status_payload: dict[str, Any]) -> bool:
        """Given a get_document() response, report whether PandaDoc
        considers it fully completed/signed. PandaDoc's terminal status for
        a completed e-signature is `document.completed`.
        """
        return document_status_payload.get("status") == "document.completed"
