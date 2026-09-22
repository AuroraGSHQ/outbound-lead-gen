from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.integrations.claude import ClaudeDrafter
from app.models import Lead, LeadStatus, Message
from app.services import winback


def _settings() -> Settings:
    return Settings(anthropic_api_key="test-key", business_name="Aurora", business_pitch="We fix funnels")


def _stub_drafter(monkeypatch):
    monkeypatch.setattr(
        ClaudeDrafter,
        "draft_client_email",
        lambda self, purpose, lead, context, profile: {"subject": "quick reconnect", "body": "Hope things are well."},
    )


def _lead(**overrides) -> Lead:
    defaults = dict(
        company_name="Old Prospect Co",
        contact_email=f"lead-{id(overrides)}@example.com",
        status=LeadStatus.CLOSED_LOST.value,
        updated_at=datetime.now(timezone.utc) - timedelta(days=45),
    )
    defaults.update(overrides)
    return Lead(**defaults)


def test_drafts_for_lead_in_the_six_week_window(db_session, monkeypatch):
    _stub_drafter(monkeypatch)
    lead = _lead(contact_email="a@example.com")
    db_session.add(lead)
    db_session.commit()

    stats = winback.check_winback_due(db_session, _settings())

    assert stats["drafted"] == 1
    message = db_session.query(Message).one()
    assert message.status == "pending_approval"
    assert message.reviewer_note == "agent:gravity:winback"


def test_skips_lead_too_recent(db_session, monkeypatch):
    _stub_drafter(monkeypatch)
    lead = _lead(contact_email="b@example.com", updated_at=datetime.now(timezone.utc) - timedelta(days=10))
    db_session.add(lead)
    db_session.commit()

    stats = winback.check_winback_due(db_session, _settings())
    assert stats["drafted"] == 0


def test_skips_lead_outside_the_window_on_the_far_side(db_session, monkeypatch):
    _stub_drafter(monkeypatch)
    lead = _lead(contact_email="c@example.com", updated_at=datetime.now(timezone.utc) - timedelta(days=90))
    db_session.add(lead)
    db_session.commit()

    stats = winback.check_winback_due(db_session, _settings())
    assert stats["drafted"] == 0


def test_skips_unsubscribed_lead(db_session, monkeypatch):
    _stub_drafter(monkeypatch)
    lead = _lead(contact_email="d@example.com", unsubscribed=True)
    db_session.add(lead)
    db_session.commit()

    stats = winback.check_winback_due(db_session, _settings())
    assert stats["drafted"] == 0


def test_does_not_redraft_a_lead_already_winback_drafted(db_session, monkeypatch):
    _stub_drafter(monkeypatch)
    lead = _lead(contact_email="e@example.com")
    db_session.add(lead)
    db_session.commit()

    first = winback.check_winback_due(db_session, _settings())
    second = winback.check_winback_due(db_session, _settings())

    assert first["drafted"] == 1
    assert second["drafted"] == 0
