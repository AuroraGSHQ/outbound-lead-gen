"""Basic coverage for the seven newest on-demand agents (Pulsar, Quasar,
Comet, Nebula, Apollo, Starlight, Supernova) — matches the style of the
other on-demand-agent tests (test_winback.py, test_actions_dispatch.py):
confirm each service function runs against minimal fixture data and
produces the expected ActionItem, without erroring.
"""
from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.integrations.claude import ClaudeDrafter
from app.models import ActionItem, AdCampaign, AdCampaignStatus, Client, ClientStatus, MetricSnapshot
from app.services import (
    brand_audit,
    campaign_launch,
    campaign_opportunities,
    longform_content,
    production,
    social_posts,
    trend_watch,
)


def _settings() -> Settings:
    return Settings(anthropic_api_key="test-key", business_name="Aurora", business_pitch="We fix funnels")


def _client(**overrides) -> Client:
    defaults = dict(company_name="Acme GC", status=ClientStatus.ACTIVE.value, notes="Grew leads 3x in Q1.")
    defaults.update(overrides)
    return Client(**defaults)


# --- Pulsar: social post drafting -------------------------------------------


def test_pulsar_drafts_social_post_for_a_client(db_session, monkeypatch):
    monkeypatch.setattr(
        ClaudeDrafter, "draft_social_caption", lambda self, client, context, platform, profile: {"caption": "We helped Acme GC grow."}
    )
    client = _client()
    db_session.add(client)
    db_session.commit()

    item = social_posts.draft_social_post(db_session, _settings(), client_id=client.id)

    assert item.status != "rejected"
    assert item.result == "We helped Acme GC grow."
    assert item.created_by == "agent:pulsar"
    assert db_session.query(ActionItem).filter(ActionItem.client_id == client.id).count() == 1


def test_pulsar_raises_for_unknown_client(db_session):
    try:
        social_posts.draft_social_post(db_session, _settings(), client_id=999)
        assert False, "expected ValueError"
    except ValueError:
        pass


# --- Quasar: campaign opportunity spotting ----------------------------------


def test_quasar_flags_the_best_performing_channel(db_session):
    snapshot = MetricSnapshot(
        date=datetime.now(timezone.utc),
        qualified_conversations=10,
        close_rate={"email": 0.1, "ads": 0.4},
        cost_per_qualified_conversation={"ads": 120.0},
    )
    db_session.add(snapshot)
    db_session.commit()

    stats = campaign_opportunities.spot_campaign_opportunities(db_session)

    assert stats["opportunities_flagged"] == 1
    item = db_session.query(ActionItem).filter(ActionItem.created_by == "agent:quasar").one()
    assert "ads" in item.title


def test_quasar_does_not_duplicate_the_same_finding(db_session):
    snapshot = MetricSnapshot(
        date=datetime.now(timezone.utc), qualified_conversations=10, close_rate={"ads": 0.4}
    )
    db_session.add(snapshot)
    db_session.commit()

    first = campaign_opportunities.spot_campaign_opportunities(db_session)
    second = campaign_opportunities.spot_campaign_opportunities(db_session)

    assert first["opportunities_flagged"] == 1
    assert second["opportunities_flagged"] == 0


def test_quasar_skips_when_no_snapshot_exists(db_session):
    stats = campaign_opportunities.spot_campaign_opportunities(db_session)
    assert stats["opportunities_flagged"] == 0


# --- Comet: trend detection ---------------------------------------------


def test_comet_flags_a_rising_trend(db_session):
    now = datetime.now(timezone.utc)
    for i, value in enumerate([3, 6, 9]):
        db_session.add(MetricSnapshot(date=now - timedelta(days=2 - i), qualified_conversations=value))
    db_session.commit()

    stats = trend_watch.check_trend(db_session, lookback=3)

    assert stats["trends_flagged"] == 1
    item = db_session.query(ActionItem).filter(ActionItem.created_by == "agent:comet").one()
    assert "up" in item.title


def test_comet_does_not_flag_a_flat_or_mixed_series(db_session):
    now = datetime.now(timezone.utc)
    for i, value in enumerate([5, 3, 8]):
        db_session.add(MetricSnapshot(date=now - timedelta(days=2 - i), qualified_conversations=value))
    db_session.commit()

    stats = trend_watch.check_trend(db_session, lookback=3)
    assert stats["trends_flagged"] == 0


def test_comet_skips_when_not_enough_history(db_session):
    db_session.add(MetricSnapshot(date=datetime.now(timezone.utc), qualified_conversations=5))
    db_session.commit()

    stats = trend_watch.check_trend(db_session, lookback=3)
    assert stats["trends_flagged"] == 0


# --- Nebula: long-form content -----------------------------------------


def test_nebula_drafts_a_client_story_piece(db_session, monkeypatch):
    monkeypatch.setattr(
        ClaudeDrafter, "draft_longform_content", lambda self, topic, context, profile: "# A Great Quarter\n\nDetails."
    )
    client = _client()
    db_session.add(client)
    db_session.commit()

    item = longform_content.draft_longform_piece(db_session, _settings(), client_id=client.id)

    assert item.created_by == "agent:nebula"
    assert "Great Quarter" in item.result


def test_nebula_drafts_from_metrics_when_no_client_given(db_session, monkeypatch):
    monkeypatch.setattr(
        ClaudeDrafter, "draft_longform_content", lambda self, topic, context, profile: "# Numbers\n\nDetails."
    )
    item = longform_content.draft_longform_piece(db_session, _settings())
    assert item.client_id is None
    assert "Numbers" in item.result


# --- Apollo: production briefs ------------------------------------------


def test_apollo_creates_a_production_brief_for_a_campaign(db_session, monkeypatch):
    monkeypatch.setattr(
        ClaudeDrafter, "draft_production_brief", lambda self, campaign, profile: "- Shot 1\n- Shot 2"
    )
    campaign = AdCampaign(platform="meta", quarter_label="2026-Q3", status=AdCampaignStatus.DRAFT.value)
    db_session.add(campaign)
    db_session.commit()

    item = production.create_production_brief(db_session, _settings(), campaign.id)

    assert item.created_by == "agent:apollo"
    assert item.payload["ad_campaign_id"] == campaign.id
    assert "Shot 1" in item.result


def test_apollo_raises_for_unknown_campaign(db_session):
    try:
        production.create_production_brief(db_session, _settings(), 999)
        assert False, "expected ValueError"
    except ValueError:
        pass


# --- Starlight: brand consistency review ---------------------------------


def test_starlight_reviews_brand_consistency_for_a_client(db_session, monkeypatch):
    monkeypatch.setattr(ClaudeDrafter, "draft_brand_review", lambda self, client, notes, profile: "Looks consistent.")
    client = _client()
    db_session.add(client)
    db_session.commit()

    item = brand_audit.check_brand_consistency(db_session, _settings(), client.id)

    assert item.created_by == "agent:starlight"
    assert item.result == "Looks consistent."


# --- Supernova: go-live checklist ----------------------------------------


def test_supernova_builds_checklist_when_campaign_ready(db_session):
    campaign = AdCampaign(
        platform="google",
        quarter_label="2026-Q3",
        status=AdCampaignStatus.READY_TO_PUBLISH.value,
        budget_monthly=1000,
        targeting={"segment": "smb"},
        creative={"headline": "h", "body": "b"},
        brief_markdown="# brief",
    )
    db_session.add(campaign)
    db_session.commit()

    item = campaign_launch.on_campaign_ready_to_publish(db_session, _settings(), campaign.id)

    assert item is not None
    assert item.created_by == "agent:supernova"
    assert "clear to publish" in item.description


def test_supernova_does_nothing_when_campaign_not_ready(db_session):
    campaign = AdCampaign(platform="google", quarter_label="2026-Q3", status=AdCampaignStatus.DRAFT.value)
    db_session.add(campaign)
    db_session.commit()

    result = campaign_launch.on_campaign_ready_to_publish(db_session, _settings(), campaign.id)
    assert result is None


def test_supernova_checklist_flags_missing_pieces(db_session):
    campaign = AdCampaign(platform="google", quarter_label="2026-Q3", status=AdCampaignStatus.READY_TO_PUBLISH.value)
    db_session.add(campaign)
    db_session.commit()

    item = campaign_launch.build_go_live_checklist(db_session, _settings(), campaign.id)
    assert "missing" in item.description.lower()


def test_supernova_does_not_duplicate_an_open_checklist(db_session):
    campaign = AdCampaign(platform="google", quarter_label="2026-Q3", status=AdCampaignStatus.READY_TO_PUBLISH.value)
    db_session.add(campaign)
    db_session.commit()

    first = campaign_launch.build_go_live_checklist(db_session, _settings(), campaign.id)
    second = campaign_launch.build_go_live_checklist(db_session, _settings(), campaign.id)
    assert first.id == second.id
    assert db_session.query(ActionItem).filter(ActionItem.created_by == "agent:supernova").count() == 1
