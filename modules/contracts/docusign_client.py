"""DocuSign API client (JWT grant OAuth + eSignature REST).

Thin httpx wrapper — dumb in/out, no business logic, no DB access.

## Auth-host assumption (read this before wiring credentials)

`settings.docusign_base_url` (default `https://demo.docusign.net/restapi`)
is the **eSignature REST API base** — it is *not* the same host DocuSign
issues OAuth tokens from. DocuSign's JWT Grant flow always talks to one of
two fixed authorization-server hosts, independent of which REST base you're
calling:

    - demo/sandbox accounts -> https://account-d.docusign.com
    - production accounts   -> https://account.docusign.com

There is no settings field for this (it isn't listed in app/config.py, and
this module isn't allowed to add one), so this client derives it from
`docusign_base_url`: if it contains `demo.docusign.net` (or `account-d`),
it uses the demo auth host; otherwise it uses the production auth host.
This is a reasonable, documented default for the common case (base URL
points at demo.docusign.net or www.docusign.net) but if a custom/regional
REST base is ever used, the auth host may need to be hardcoded/overridden
directly here.

## JWT Grant flow (https://developers.docusign.com/platform/auth/jwt/)

1. Build a JWT signed with the integration's RSA private key:
   - iss = docusign_integration_key (the Integration/Client Key)
   - sub = docusign_user_id (the impersonated user's GUID)
   - aud = the auth host (account-d.docusign.com / account.docusign.com)
   - scope = "signature impersonation"
   - iat/exp = now / now + 1 hour
2. POST to `https://{auth_host}/oauth/token` with
   `grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer&assertion={jwt}`
3. Use the returned `access_token` as a Bearer token against the REST API
   base (`docusign_base_url`).

Note: the very first time a JWT grant is used for a given integration key +
user, DocuSign requires one interactive consent grant (visiting a URL in a
browser) before server-to-server JWT auth will succeed. That's a one-time
setup step outside this module's scope.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx
import jwt as pyjwt

DOCUSIGN_AUTH_HOST_DEMO = "account-d.docusign.com"
DOCUSIGN_AUTH_HOST_PROD = "account.docusign.com"
JWT_SCOPE = "signature impersonation"
JWT_LIFETIME_SECONDS = 3600


def _resolve_auth_host(base_url: str) -> str:
    lowered = (base_url or "").lower()
    if "demo.docusign.net" in lowered or "account-d" in lowered:
        return DOCUSIGN_AUTH_HOST_DEMO
    return DOCUSIGN_AUTH_HOST_PROD


class DocuSignClient:
    def __init__(
        self,
        *,
        integration_key: str,
        user_id: str,
        account_id: str,
        private_key_path: str,
        base_url: str,
        timeout: float = 30.0,
    ):
        if not integration_key or not user_id or not account_id:
            raise ValueError("DocuSign integration_key, user_id and account_id are required")
        self._integration_key = integration_key
        self._user_id = user_id
        self._account_id = account_id
        self._private_key_path = private_key_path
        self._base_url = base_url.rstrip("/")
        self._auth_host = _resolve_auth_host(base_url)
        self._client = httpx.Client(timeout=timeout)
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------
    def _build_jwt(self) -> str:
        private_key = Path(self._private_key_path).read_text()
        now = int(time.time())
        claims = {
            "iss": self._integration_key,
            "sub": self._user_id,
            "aud": self._auth_host,
            "iat": now,
            "exp": now + JWT_LIFETIME_SECONDS,
            "scope": JWT_SCOPE,
        }
        return pyjwt.encode(claims, private_key, algorithm="RS256")

    def _fetch_access_token(self) -> str:
        assertion = self._build_jwt()
        resp = self._client.post(
            f"https://{self._auth_host}/oauth/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["access_token"]
        # Refresh a little early to avoid racing expiry.
        self._token_expires_at = time.time() + data.get("expires_in", JWT_LIFETIME_SECONDS) - 60
        return self._access_token

    def _get_access_token(self) -> str:
        if self._access_token is None or time.time() >= self._token_expires_at:
            return self._fetch_access_token()
        return self._access_token

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_access_token()}",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Envelopes
    # ------------------------------------------------------------------
    def create_envelope_from_template(
        self,
        *,
        template_id: str,
        filled_data: dict[str, Any],
        recipient_email: str,
        recipient_name: str,
        role_name: str = "Client",
        recipient_id: str = "1",
    ) -> dict[str, Any]:
        """Create an envelope from a template in `created` (draft, unsent)
        status. `filled_data` is passed through as template text tab values
        keyed by field label — the template's tab labels must match the
        keys used here.
        """
        text_tabs = [
            {"tabLabel": str(key), "value": "" if value is None else str(value)}
            for key, value in filled_data.items()
        ]
        payload: dict[str, Any] = {
            "templateId": template_id,
            "status": "created",
            "templateRoles": [
                {
                    "email": recipient_email,
                    "name": recipient_name,
                    "roleName": role_name,
                    "recipientId": recipient_id,
                    "tabs": {"textTabs": text_tabs},
                }
            ],
        }
        resp = self._client.post(
            f"{self._base_url}/v2.1/accounts/{self._account_id}/envelopes",
            json=payload,
            headers=self._auth_headers(),
        )
        resp.raise_for_status()
        return resp.json()

    def get_envelope(self, envelope_id: str) -> dict[str, Any]:
        resp = self._client.get(
            f"{self._base_url}/v2.1/accounts/{self._account_id}/envelopes/{envelope_id}",
            headers=self._auth_headers(),
        )
        resp.raise_for_status()
        return resp.json()

    def send_envelope(self, envelope_id: str) -> dict[str, Any]:
        """Move a draft (`created`) envelope to `sent`, the actual send step."""
        resp = self._client.put(
            f"{self._base_url}/v2.1/accounts/{self._account_id}/envelopes/{envelope_id}",
            json={"status": "sent"},
            headers=self._auth_headers(),
        )
        resp.raise_for_status()
        return resp.json()

    def is_signed(self, envelope_status_payload: dict[str, Any]) -> bool:
        """DocuSign's terminal status for a fully-executed envelope is
        `completed`.
        """
        return envelope_status_payload.get("status") == "completed"
