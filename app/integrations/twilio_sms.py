"""Twilio — SMS send/receive and outbound voice calls, for Peitho's phone
channel. Talks to Twilio's REST API directly over httpx (no twilio SDK
dependency), the same style as the CRM integrations.

Outbound voice here is a one-way message: Twilio calls the lead and plays
back an audio clip (synthesized separately by ElevenLabs) via TwiML <Play>.
This is not a live two-way phone conversation — that needs Twilio Media
Streams plus real-time speech-to-text, well beyond this integration.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from xml.sax.saxutils import escape

import httpx

_API_BASE = "https://api.twilio.com/2010-04-01/Accounts"


class TwilioError(RuntimeError):
    pass


def _auth_header(account_sid: str, auth_token: str) -> dict:
    token = base64.b64encode(f"{account_sid}:{auth_token}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _require_config(account_sid: str, auth_token: str, from_number: str, to_number: str) -> None:
    if not account_sid or not auth_token:
        raise TwilioError("TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN are not configured.")
    if not from_number:
        raise TwilioError("TWILIO_FROM_NUMBER is not configured.")
    if not to_number:
        raise TwilioError("This lead has no phone number on file.")


def send_sms(account_sid: str, auth_token: str, from_number: str, to_number: str, body: str) -> dict:
    _require_config(account_sid, auth_token, from_number, to_number)
    resp = httpx.post(
        f"{_API_BASE}/{account_sid}/Messages.json",
        headers=_auth_header(account_sid, auth_token),
        data={"From": from_number, "To": to_number, "Body": body},
        timeout=15,
    )
    if resp.status_code >= 300:
        raise TwilioError(f"Twilio SMS send failed ({resp.status_code}): {resp.text[:300]}")
    return resp.json()


def place_call_with_audio(account_sid: str, auth_token: str, from_number: str, to_number: str, audio_url: str) -> dict:
    _require_config(account_sid, auth_token, from_number, to_number)
    twiml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Play>{escape(audio_url)}</Play></Response>'
    resp = httpx.post(
        f"{_API_BASE}/{account_sid}/Calls.json",
        headers=_auth_header(account_sid, auth_token),
        data={"From": from_number, "To": to_number, "Twiml": twiml},
        timeout=15,
    )
    if resp.status_code >= 300:
        raise TwilioError(f"Twilio call failed ({resp.status_code}): {resp.text[:300]}")
    return resp.json()


def validate_webhook_signature(auth_token: str, url: str, params: dict, signature: str) -> bool:
    """Twilio's documented X-Twilio-Signature check: URL + sorted key/value
    pairs concatenated, HMAC-SHA1 with the auth token, base64-encoded."""
    if not auth_token or not signature:
        return False
    payload = url
    for key in sorted(params.keys()):
        payload += key + str(params[key])
    computed = base64.b64encode(hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()).decode()
    return hmac.compare_digest(computed, signature)
