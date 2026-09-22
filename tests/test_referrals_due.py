from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.integrations.claude import ClaudeDrafter
from app.models import ActionItem, Client, ClientStatus
from app.services import referrals


def _settings() -> Settings:
    return Settings(anthropic_api_key="test-key", business_name="Aurora", business_pitch="We fix funnels")


def _stub_referral_ask(monkeypatch):
    monkeypatch.setattr(
        ClaudeDrafter,
        "draft_referral_ask",
        lambda self, client, numbers, profile: {
            "ask_script": "Who are two operators you know?",
            "forwardable_message": "Hey, meet Aurora — they fixed our lead response time.",
        },
    )


def test_client_past_90_days_gets_a_reminder(db_session, monkeypatch):
    _stub_referral_ask(monkeypatch)
    client = Client(
        company_name="Old Client Co",
        status=ClientStatus.ACTIVE.value,
        start_date=datetime.now(timezone.utc) - timedelta(days=95),
    )
    db_session.add(client)
    db_session.commit()

    stats = referrals.check_referral_reviews_due(db_session, _settings())

    assert stats["reminders_created"] == 1
    items = db_session.query(ActionItem).filter(ActionItem.client_id == client.id).all()
    assert len(items) == 1
    assert "Who are two operators" in items[0].result


def test_client_under_90_days_is_not_reminded_yet(db_session, monkeypatch):
    _stub_referral_ask(monkeypatch)
    client = Client(
        company_name="New Client Co",
        status=ClientStatus.ACTIVE.value,
        start_date=datetime.now(timezone.utc) - timedelta(days=10),
    )
    db_session.add(client)
    db_session.commit()

    stats = referrals.check_referral_reviews_due(db_session, _settings())

    assert stats["reminders_created"] == 0


def test_prospect_status_client_is_ignored_even_if_old(db_session, monkeypatch):
    _stub_referral_ask(monkeypatch)
    client = Client(
        company_name="Still Just A Prospect",
        status=ClientStatus.PROSPECT.value,
        start_date=datetime.now(timezone.utc) - timedelta(days=200),
    )
    db_session.add(client)
    db_session.commit()

    stats = referrals.check_referral_reviews_due(db_session, _settings())

    assert stats["reminders_created"] == 0


def test_client_already_asked_is_not_reminded_twice(db_session, monkeypatch):
    _stub_referral_ask(monkeypatch)
    client = Client(
        company_name="Repeat Co",
        status=ClientStatus.ACTIVE.value,
        start_date=datetime.now(timezone.utc) - timedelta(days=200),
    )
    db_session.add(client)
    db_session.commit()

    first = referrals.check_referral_reviews_due(db_session, _settings())
    second = referrals.check_referral_reviews_due(db_session, _settings())

    assert first["reminders_created"] == 1
    assert second["reminders_created"] == 0
