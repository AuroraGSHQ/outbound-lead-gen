from unittest.mock import patch

from app.config import Settings
from app.models import Lead, Message, MessageChannel, MessageStatus
from app.services import phone_outreach


def _settings() -> Settings:
    return Settings()


def _lead(**overrides) -> Lead:
    defaults = dict(company_name="Acme GC", contact_name="Jane Doe", contact_email="jane@acme.test", contact_phone="+15550002222")
    defaults.update(overrides)
    return Lead(**defaults)


def test_draft_sms_requires_phone_number(db_session):
    lead = _lead(contact_phone="")
    db_session.add(lead)
    db_session.commit()

    try:
        phone_outreach.draft_sms_for_lead(db_session, _settings(), lead.id)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "no phone number" in str(exc)


def test_draft_sms_creates_pending_message_with_opt_out(db_session):
    lead = _lead()
    db_session.add(lead)
    db_session.commit()

    with patch("app.services.phone_outreach.ClaudeDrafter") as mock_drafter_cls:
        mock_drafter_cls.return_value.draft_sms_first_touch.return_value = "Hi Jane, quick idea for Acme."
        message = phone_outreach.draft_sms_for_lead(db_session, _settings(), lead.id)

    assert message.channel == MessageChannel.SMS.value
    assert message.status == MessageStatus.PENDING_APPROVAL.value
    assert "Reply STOP to opt out." in message.body
    assert "Hi Jane" in message.body


def test_draft_call_requires_phone_number(db_session):
    lead = _lead(contact_phone="")
    db_session.add(lead)
    db_session.commit()

    try:
        phone_outreach.draft_call_for_lead(db_session, _settings(), lead.id)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "no phone number" in str(exc)


def test_draft_call_creates_voice_message(db_session):
    lead = _lead()
    db_session.add(lead)
    db_session.commit()

    with patch("app.services.phone_outreach.ClaudeDrafter") as mock_drafter_cls:
        mock_drafter_cls.return_value.draft_call_script.return_value = "Hey Jane, quick one for you."
        message = phone_outreach.draft_call_for_lead(db_session, _settings(), lead.id)

    assert message.channel == MessageChannel.VOICE.value
    assert message.status == MessageStatus.PENDING_APPROVAL.value
    assert message.body == "Hey Jane, quick one for you."


def test_receive_sms_reply_matches_by_phone(db_session):
    lead = _lead()
    db_session.add(lead)
    db_session.commit()

    message = phone_outreach.receive_sms_reply(db_session, "+15550002222", "sure, call me")

    assert message is not None
    assert message.body == "sure, call me"
    assert message.channel == MessageChannel.SMS.value
    assert message.conversation.lead_id == lead.id


def test_receive_sms_reply_drops_unknown_number(db_session):
    message = phone_outreach.receive_sms_reply(db_session, "+15559999999", "hello?")
    assert message is None
    assert db_session.query(Message).count() == 0
