import pytest

from app.integrations.site_checks import SiteCheckResult
from app.models import ActionItem
from app.services import scanner


def _fake_check(**overrides):
    defaults = dict(
        reachable=True, load_time_ms=1200, mobile_ok=True, tracking_present=True,
        click_to_call_present=True, has_contact_form=True,
    )
    defaults.update(overrides)

    def _check(domain, **kwargs):
        return SiteCheckResult(domain=domain, **defaults)

    return _check


def test_clean_site_has_no_faults(db_session, monkeypatch):
    monkeypatch.setattr(scanner, "check_site", _fake_check())
    target = scanner.queue_target(db_session, domain="clean.example.com")

    result = scanner.run_scan(db_session, target.id)

    assert result.fault_list == []
    assert result.leak_score == 0.0
    assert result.scanned is True


def test_faulty_site_flags_each_automated_check(db_session, monkeypatch):
    monkeypatch.setattr(
        scanner,
        "check_site",
        _fake_check(mobile_ok=False, tracking_present=False, click_to_call_present=False, load_time_ms=5000),
    )
    target = scanner.queue_target(db_session, domain="slow.example.com")

    result = scanner.run_scan(db_session, target.id)

    assert len(result.fault_list) == 4  # slow load, no mobile, no tracking, no click-to-call
    assert result.leak_score == pytest.approx(4 / 5)


def test_unreachable_site_flags_a_single_fault(db_session, monkeypatch):
    monkeypatch.setattr(scanner, "check_site", lambda domain, **kw: SiteCheckResult(domain=domain, reachable=False, error="timeout"))
    target = scanner.queue_target(db_session, domain="down.example.com")

    result = scanner.run_scan(db_session, target.id)

    assert result.fault_list == ["Site unreachable: timeout"]


def test_run_scan_creates_a_verification_task_never_auto_executable(db_session, monkeypatch):
    monkeypatch.setattr(scanner, "check_site", _fake_check(mobile_ok=False))
    target = scanner.queue_target(db_session, domain="needs-check.example.com")

    scanner.run_scan(db_session, target.id)

    items = db_session.query(ActionItem).filter(ActionItem.category == "scanner_verify").all()
    assert len(items) == 1
    assert items[0].auto_executable is False


def test_draft_outreach_refuses_unverified_scan(db_session, monkeypatch):
    from app.config import Settings

    monkeypatch.setattr(scanner, "check_site", _fake_check(mobile_ok=False))
    target = scanner.queue_target(db_session, domain="unverified.example.com")
    scanner.run_scan(db_session, target.id)

    settings = Settings(anthropic_api_key="test", business_name="Test Co", business_pitch="We do things")
    with pytest.raises(ValueError, match="unverified"):
        scanner.draft_fault_led_outreach(db_session, settings, target.id, contact_email="prospect@example.com")


def test_verified_scan_allows_outreach_draft(db_session, monkeypatch):
    from app.config import Settings
    from app.integrations.claude import ClaudeDrafter

    monkeypatch.setattr(scanner, "check_site", _fake_check(mobile_ok=False))
    monkeypatch.setattr(
        ClaudeDrafter,
        "draft_fault_led_email",
        lambda self, lead, faults, profile: {"subject": "your site", "body": "Noticed something."},
    )
    target = scanner.queue_target(db_session, domain="verified.example.com")
    scanner.run_scan(db_session, target.id)
    scanner.mark_verified(db_session, target.id, user_id=1, notes="Called and confirmed.")

    settings = Settings(anthropic_api_key="test", business_name="Test Co", business_pitch="We do things")
    message = scanner.draft_fault_led_outreach(
        db_session, settings, target.id, contact_email="prospect@example.com", contact_name="Sam"
    )
    assert message.status == "pending_approval"
    assert "Noticed something." in message.body
