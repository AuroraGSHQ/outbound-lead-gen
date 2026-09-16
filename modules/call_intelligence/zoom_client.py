"""Thin client for Zoom Server-to-Server OAuth + pulling a meeting's cloud
recording transcript.

Kept dumb on purpose (matches the app/integrations/*.py and
modules/dialer/client.py pattern): builds a request, calls Zoom, normalizes
the response. No DB access, no webhook-signature verification (that lives in
router.py), no summarization logic (that lives in summarizer.py).
"""
from __future__ import annotations

from typing import Any

import httpx

ZOOM_OAUTH_URL = "https://zoom.us/oauth/token"
ZOOM_API_BASE_URL = "https://api.zoom.us/v2"

# Zoom's recording_files[].recording_type values that carry a transcript.
_TRANSCRIPT_RECORDING_TYPES = {"audio_transcript", "transcript", "closed_caption"}


class ZoomClientError(RuntimeError):
    """Raised when Zoom rejects a token or recording-info request."""


class ZoomClient:
    """Normalized Zoom Server-to-Server OAuth + recordings client.

    Usage:
        client = ZoomClient(settings)
        transcript = client.get_meeting_transcript(meeting_id)
        client.close()
    """

    def __init__(self, settings: Any, timeout: float = 30.0):
        self._account_id = settings.zoom_account_id
        self._client_id = settings.zoom_client_id
        self._client_secret = settings.zoom_client_secret
        self._client = httpx.Client(timeout=timeout)
        self._access_token: str | None = None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ZoomClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def _get_access_token(self) -> str:
        """Server-to-Server OAuth: exchange account credentials for a short-lived
        access token. Not cached across calls beyond a single client instance's
        lifetime — fine for a webhook-triggered client with a short lifespan.
        """
        if self._access_token:
            return self._access_token

        if not (self._account_id and self._client_id and self._client_secret):
            raise ZoomClientError(
                "ZOOM_ACCOUNT_ID / ZOOM_CLIENT_ID / ZOOM_CLIENT_SECRET are required"
            )

        resp = self._client.post(
            ZOOM_OAUTH_URL,
            params={"grant_type": "account_credentials", "account_id": self._account_id},
            auth=(self._client_id, self._client_secret),
        )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ZoomClientError(f"Zoom OAuth token request failed: {exc.response.text}") from exc

        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise ZoomClientError(f"Zoom OAuth response missing access_token: {data}")
        self._access_token = token
        return token

    def get_recording_files(self, meeting_id: str) -> list[dict[str, Any]]:
        """GET /v2/meetings/{meetingId}/recordings, returns recording_files list."""
        token = self._get_access_token()
        resp = self._client.get(
            f"{ZOOM_API_BASE_URL}/meetings/{meeting_id}/recordings",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ZoomClientError(f"Zoom recordings lookup failed: {exc.response.text}") from exc
        data = resp.json()
        return list(data.get("recording_files") or [])

    def get_meeting_transcript(self, meeting_id: str) -> str:
        """Find the transcript recording file for a meeting and download its
        contents as text. Returns "" if no transcript file is found.
        """
        token = self._get_access_token()
        files = self.get_recording_files(meeting_id)
        transcript_file = next(
            (
                f
                for f in files
                if str(f.get("recording_type", "")).lower() in _TRANSCRIPT_RECORDING_TYPES
            ),
            None,
        )
        if transcript_file is None:
            return ""

        download_url = transcript_file.get("download_url", "")
        if not download_url:
            return ""

        resp = self._client.get(
            download_url,
            headers={"Authorization": f"Bearer {token}"},
            params={"access_token": token},
        )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ZoomClientError(f"Zoom transcript download failed: {exc.response.text}") from exc
        return resp.text
