import pytest

from app.integrations.site_checks import SiteCheckResult
from app.models import ActionStatus
from app.services import actions, scanner


def test_unknown_handler_raises(db_session):
    item = actions.create_action_item(
        db_session, title="Do something odd", category="admin", handler="not_a_real_handler", auto_executable=True
    )
    with pytest.raises(ValueError, match="No automated handler"):
        actions.execute_action_item(db_session, settings=None, action_item_id=item.id)


def test_mark_done_sets_status_and_result_note(db_session):
    item = actions.create_action_item(db_session, title="Manual task", category="admin")
    updated = actions.mark_done(db_session, item.id, "handled by hand")
    assert updated.status == ActionStatus.DONE.value
    assert updated.result == "handled by hand"


def test_assign_sets_assignee(db_session):
    item = actions.create_action_item(db_session, title="Assign me", category="admin")
    updated = actions.assign(db_session, item.id, 42)
    assert updated.assignee_user_id == 42


def test_list_action_items_filters_by_status_and_category(db_session):
    actions.create_action_item(db_session, title="A", category="ads")
    done_item = actions.create_action_item(db_session, title="B", category="referral")
    actions.mark_done(db_session, done_item.id)

    assert len(actions.list_action_items(db_session, category="ads")) == 1
    assert len(actions.list_action_items(db_session, status=ActionStatus.DONE.value)) == 1
    assert len(actions.list_action_items(db_session)) == 2


def test_run_scanner_scan_handler_executes_without_touching_claude(db_session, monkeypatch):
    monkeypatch.setattr(
        scanner,
        "check_site",
        lambda domain, **kw: SiteCheckResult(
            domain=domain, reachable=True, load_time_ms=500, mobile_ok=True,
            tracking_present=True, click_to_call_present=True, has_contact_form=True,
        ),
    )
    item = actions.create_action_item(
        db_session,
        title="Scan them",
        category="scanner_verify",
        handler="run_scanner_scan",
        auto_executable=True,
        payload={"domain": "test.example.com"},
    )

    result_item = actions.execute_action_item(db_session, settings=None, action_item_id=item.id)

    assert result_item.status == ActionStatus.DONE.value
    assert "Scan complete" in result_item.result


def test_skipped_handler_still_marks_done_with_a_note(db_session):
    item = actions.create_action_item(
        db_session,
        title="Scan with no domain",
        category="scanner_verify",
        handler="run_scanner_scan",
        auto_executable=True,
        payload={},  # no domain -> handler skips gracefully rather than raising
    )
    result_item = actions.execute_action_item(db_session, settings=None, action_item_id=item.id)
    assert result_item.status == ActionStatus.DONE.value
    assert "No domain" in result_item.result


def test_handler_exception_leaves_item_open_with_failure_recorded(db_session, monkeypatch):
    def _boom(domain, **kw):
        raise RuntimeError("site unreachable and then some")

    monkeypatch.setattr(scanner, "check_site", _boom)
    item = actions.create_action_item(
        db_session,
        title="Scan a domain that blows up",
        category="scanner_verify",
        handler="run_scanner_scan",
        auto_executable=True,
        payload={"domain": "explodes.example.com"},
    )

    result_item = actions.execute_action_item(db_session, settings=None, action_item_id=item.id)

    assert result_item.status == ActionStatus.PROPOSED.value
    assert "Failed" in result_item.result
