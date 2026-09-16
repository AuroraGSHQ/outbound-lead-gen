"""Thin client for placing an AI-agent-handled outbound call via Vapi or
Retell, whichever `settings.voice_agent_provider` names.

Kept dumb on purpose (matches the app/integrations/*.py pattern): builds a
request, calls the provider, normalizes the response. No DB access, no
segment/script logic, no retry/cooldown/window logic — that all lives in
service.py.
"""
from __future__ import annotations

from typing import Any

import httpx

VAPI_BASE_URL = "https://api.vapi.ai"
RETELL_BASE_URL = "https://api.retellai.com"


class VoiceAgentError(RuntimeError):
    """Raised when the voice agent provider rejects or fails a call request."""


class VoiceAgentClient:
    """Normalized outbound-call client over Vapi or Retell.

    Usage:
        client = VoiceAgentClient(settings)
        result = client.place_call(to_number="+15551234567", context={...})
        # result == {"external_call_id": "...", "status": "..."}
        client.close()
    """

    def __init__(self, settings: Any, timeout: float = 30.0):
        provider = (settings.voice_agent_provider or "vapi").strip().lower()
        if provider not in ("vapi", "retell"):
            raise ValueError(f"Unknown voice_agent_provider: {provider!r}")
        self._provider = provider
        self._settings = settings

        if provider == "vapi":
            if not settings.vapi_api_key:
                raise ValueError("VAPI_API_KEY is required when voice_agent_provider=vapi")
            self._client = httpx.Client(
                base_url=VAPI_BASE_URL,
                timeout=timeout,
                headers={
                    "Authorization": f"Bearer {settings.vapi_api_key}",
                    "Content-Type": "application/json",
                },
            )
        else:
            if not settings.retell_api_key:
                raise ValueError("RETELL_API_KEY is required when voice_agent_provider=retell")
            self._client = httpx.Client(
                base_url=RETELL_BASE_URL,
                timeout=timeout,
                headers={
                    "Authorization": f"Bearer {settings.retell_api_key}",
                    "Content-Type": "application/json",
                },
            )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "VoiceAgentClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def place_call(
        self,
        *,
        to_number: str,
        context: dict[str, Any] | None = None,
        first_message: str | None = None,
    ) -> dict[str, Any]:
        """Place an outbound AI-agent call. `context` is a flat dict of
        dynamic variables (segment, script/objective text, contact name,
        company, etc.) injected into the assistant/agent without needing a
        separate assistant per segment.

        Returns {"external_call_id": str, "status": str}.
        """
        if self._provider == "vapi":
            return self._place_call_vapi(to_number, context or {}, first_message)
        return self._place_call_retell(to_number, context or {}, first_message)

    def _place_call_vapi(
        self, to_number: str, context: dict[str, Any], first_message: str | None
    ) -> dict[str, Any]:
        settings = self._settings
        assistant_overrides: dict[str, Any] = {"variableValues": context}
        if first_message:
            assistant_overrides["firstMessage"] = first_message

        body = {
            "assistantId": settings.vapi_assistant_id,
            "customer": {"number": to_number},
            "assistantOverrides": assistant_overrides,
        }
        # NOTE: Vapi's /call endpoint normally wants an outbound `phoneNumberId`
        # (a Vapi-side id for a number you've imported/bought there), not the
        # raw E.164 twilio_phone_number. Settings only stores the raw number,
        # so we omit phoneNumberId and rely on the Vapi assistant's own
        # configured/default outbound number. If Vapi requires an explicit
        # phoneNumberId for this account, that's a settings gap for the
        # integration pass (see final report).
        resp = self._client.post("/call", json=body)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise VoiceAgentError(f"Vapi call request failed: {exc.response.text}") from exc
        data = resp.json()
        return {
            "external_call_id": str(data.get("id", "")),
            "status": str(data.get("status", "queued")),
        }

    def _place_call_retell(
        self, to_number: str, context: dict[str, Any], first_message: str | None
    ) -> dict[str, Any]:
        settings = self._settings
        body: dict[str, Any] = {
            "agent_id": settings.retell_agent_id,
            "from_number": settings.twilio_phone_number,
            "to_number": to_number,
            "retell_llm_dynamic_variables": context,
        }
        if first_message:
            body["retell_llm_dynamic_variables"]["first_message"] = first_message

        resp = self._client.post("/v2/create-phone-call", json=body)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise VoiceAgentError(f"Retell call request failed: {exc.response.text}") from exc
        data = resp.json()
        return {
            "external_call_id": str(data.get("call_id", "")),
            "status": str(data.get("call_status", "registered")),
        }
