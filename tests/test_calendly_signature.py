import hashlib
import hmac

from app.integrations.calendly import (
    extract_event_uri,
    extract_invitee_email,
    verify_signature,
)


def _sign(body: bytes, signing_key: str, timestamp: str = "1700000000") -> str:
    signed_payload = f"{timestamp}.{body.decode()}"
    sig = hmac.new(signing_key.encode(), signed_payload.encode(), hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={sig}"


def test_valid_signature_passes():
    body = b'{"event": "invitee.created"}'
    key = "supersecret"
    header = _sign(body, key)
    assert verify_signature(body, header, key) is True


def test_tampered_body_fails():
    body = b'{"event": "invitee.created"}'
    key = "supersecret"
    header = _sign(body, key)
    tampered = b'{"event": "invitee.cancelled"}'
    assert verify_signature(tampered, header, key) is False


def test_wrong_key_fails():
    body = b'{"event": "invitee.created"}'
    header = _sign(body, "supersecret")
    assert verify_signature(body, header, "wrongkey") is False


def test_missing_header_fails():
    assert verify_signature(b"{}", "", "supersecret") is False


def test_extract_invitee_email():
    payload = {"payload": {"email": "prospect@example.com"}}
    assert extract_invitee_email(payload) == "prospect@example.com"


def test_extract_event_uri():
    payload = {"payload": {"event": "https://api.calendly.com/scheduled_events/abc123"}}
    assert extract_event_uri(payload) == "https://api.calendly.com/scheduled_events/abc123"
