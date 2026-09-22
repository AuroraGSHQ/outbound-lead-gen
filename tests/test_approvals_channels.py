from unittest.mock import patch

from app.config import Settings
from app.models import Conversation, Lead, Message, MessageChannel, MessageDirection, MessageStatus
from app.services import approvals


def _settings(**overrides) -> Settings:
    defaults = dict(
        twilio_account_sid="SIDXXX",
        twilio_auth_token="tokenXXX",
        twilio_from_number="+15550001111",
        elevenlabs_api_key="elkey",
        elevenlabs_voice_id="voice123",
        public_base_url="https://cosmos.example.com",
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _lead_with_conversation(session, **lead_overrides):
    defaults = dict(company_name="Acme GC", contact_name="Jane Doe", contact_email="jane@acme.test", contact_phone="+15550002222")
    defaults.update(lead_overrides)
    lead = Lead(**defaults)
    session.add(lead)
    session.commit()
    conversation = Conversation(lead_id=lead.id)
    session.add(conversation)
    session.commit()
    return lead, conversation


def test_send_message_dispatches_sms(db_session):
    lead, conversation = _lead_with_conversation(db_session)
    message = Message(
        conversation_id=conversation.id,
        direction=MessageDirection.OUTBOUND.value,
        channel=MessageChannel.SMS.value,
        body="hi there",
        status=MessageStatus.APPROVED.value,
    )
    db_session.add(message)
    db_session.commit()

    with patch("app.services.approvals.twilio_sms.send_sms", return_value={"sid": "SM123"}) as mock_send:
        result = approvals.send_message(db_session, _settings(), message.id)

    mock_send.assert_called_once_with("SIDXXX", "tokenXXX", "+15550001111", "+15550002222", "hi there")
    assert result.status == MessageStatus.SENT.value
    assert result.twilio_sid == "SM123"


def test_send_message_dispatches_voice(db_session, tmp_path):
    lead, conversation = _lead_with_conversation(db_session)
    message = Message(
        conversation_id=conversation.id,
        direction=MessageDirection.OUTBOUND.value,
        channel=MessageChannel.VOICE.value,
        body="Hey Jane, quick one for you.",
        status=MessageStatus.APPROVED.value,
    )
    db_session.add(message)
    db_session.commit()

    settings = _settings(voice_clip_dir=str(tmp_path))
    with patch("app.services.approvals.elevenlabs.synthesize_to_file") as mock_synth, patch(
        "app.services.approvals.twilio_sms.place_call_with_audio", return_value={"sid": "CA123"}
    ) as mock_call:
        result = approvals.send_message(db_session, settings, message.id)

    mock_synth.assert_called_once()
    mock_call.assert_called_once_with(
        "SIDXXX", "tokenXXX", "+15550001111", "+15550002222",
        f"https://cosmos.example.com/voice-clips/{message.id}.mp3",
    )
    assert result.status == MessageStatus.SENT.value
    assert result.twilio_sid == "CA123"


def test_send_message_voice_requires_public_base_url(db_session, tmp_path):
    lead, conversation = _lead_with_conversation(db_session)
    message = Message(
        conversation_id=conversation.id,
        direction=MessageDirection.OUTBOUND.value,
        channel=MessageChannel.VOICE.value,
        body="Hey Jane.",
        status=MessageStatus.APPROVED.value,
    )
    db_session.add(message)
    db_session.commit()

    settings = _settings(public_base_url="", voice_clip_dir=str(tmp_path))
    with patch("app.services.approvals.elevenlabs.synthesize_to_file"):
        try:
            approvals.send_message(db_session, settings, message.id)
            assert False, "expected ValueError"
        except ValueError as exc:
            assert "PUBLIC_BASE_URL" in str(exc)


def test_send_message_blocks_unsubscribed_lead_regardless_of_channel(db_session):
    lead, conversation = _lead_with_conversation(db_session, unsubscribed=True)
    message = Message(
        conversation_id=conversation.id,
        direction=MessageDirection.OUTBOUND.value,
        channel=MessageChannel.SMS.value,
        body="hi",
        status=MessageStatus.APPROVED.value,
    )
    db_session.add(message)
    db_session.commit()

    with patch("app.services.approvals.twilio_sms.send_sms") as mock_send:
        result = approvals.send_message(db_session, _settings(), message.id)

    mock_send.assert_not_called()
    assert result.status == MessageStatus.REJECTED.value
