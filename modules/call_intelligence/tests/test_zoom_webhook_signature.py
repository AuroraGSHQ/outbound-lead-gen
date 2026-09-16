"""Unit tests for Zoom webhook signature verification and the URL-validation
handshake — no network, no DB.
"""
from __future__ import annotations

import hashlib
import hmac

from modules.call_intelligence.router import (
    build_zoom_url_validation_response,
    verify_zoom_signature,
)

SECRET = "test-zoom-secret-token"


def _sign(timestamp: str, raw_body: bytes, secret: str = SECRET) -> str:
    message = f"v0:{timestamp}:{raw_body.decode('utf-8')}"
    return "v0=" + hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def test_verify_zoom_signature_accepts_valid_signature():
    raw_body = b'{"event": "recording.completed"}'
    timestamp = "1700000000"
    signature = _sign(timestamp, raw_body)

    assert verify_zoom_signature(raw_body, timestamp, signature, SECRET) is True


def test_verify_zoom_signature_rejects_wrong_secret():
    raw_body = b'{"event": "recording.completed"}'
    timestamp = "1700000000"
    signature = _sign(timestamp, raw_body, secret="wrong-secret")

    assert verify_zoom_signature(raw_body, timestamp, signature, SECRET) is False


def test_verify_zoom_signature_rejects_tampered_body():
    timestamp = "1700000000"
    signature = _sign(timestamp, b'{"event": "recording.completed"}')

    tampered_body = b'{"event": "recording.deleted"}'
    assert verify_zoom_signature(tampered_body, timestamp, signature, SECRET) is False


def test_verify_zoom_signature_rejects_missing_pieces():
    raw_body = b"{}"
    assert verify_zoom_signature(raw_body, "", "v0=abc", SECRET) is False
    assert verify_zoom_signature(raw_body, "1700000000", "", SECRET) is False
    assert verify_zoom_signature(raw_body, "1700000000", "v0=abc", "") is False


def test_build_zoom_url_validation_response_matches_zoom_scheme():
    plain_token = "abc123plaintoken"
    response = build_zoom_url_validation_response(plain_token, SECRET)

    expected_encrypted = hmac.new(
        SECRET.encode("utf-8"), plain_token.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    assert response == {"plainToken": plain_token, "encryptedToken": expected_encrypted}
