import pytest

from app.integrations import twilio_sms


def test_send_sms_requires_credentials():
    with pytest.raises(twilio_sms.TwilioError, match="TWILIO_ACCOUNT_SID"):
        twilio_sms.send_sms("", "", "+15550001111", "+15550002222", "hi")


def test_send_sms_requires_from_number():
    with pytest.raises(twilio_sms.TwilioError, match="TWILIO_FROM_NUMBER"):
        twilio_sms.send_sms("SIDXXX", "tokenXXX", "", "+15550002222", "hi")


def test_send_sms_requires_recipient_phone():
    with pytest.raises(twilio_sms.TwilioError, match="no phone number"):
        twilio_sms.send_sms("SIDXXX", "tokenXXX", "+15550001111", "", "hi")


def test_place_call_requires_credentials():
    with pytest.raises(twilio_sms.TwilioError, match="TWILIO_ACCOUNT_SID"):
        twilio_sms.place_call_with_audio("", "", "+15550001111", "+15550002222", "https://x/y.mp3")


def test_validate_webhook_signature_matches_twilio_algorithm():
    auth_token = "test_token"
    url = "https://example.com/webhooks/twilio-sms"
    params = {"From": "+15550002222", "Body": "hi there"}

    import base64
    import hashlib
    import hmac

    payload = url
    for key in sorted(params.keys()):
        payload += key + params[key]
    expected = base64.b64encode(hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()).decode()

    assert twilio_sms.validate_webhook_signature(auth_token, url, params, expected) is True
    assert twilio_sms.validate_webhook_signature(auth_token, url, params, "wrong-signature") is False


def test_validate_webhook_signature_rejects_missing_token_or_signature():
    assert twilio_sms.validate_webhook_signature("", "https://x", {}, "sig") is False
    assert twilio_sms.validate_webhook_signature("token", "https://x", {}, "") is False
