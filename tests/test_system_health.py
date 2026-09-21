from app.config import Settings
from app.models import ActionItem, ActionStatus
from app.services import system_health


def _settings() -> Settings:
    return Settings()


def test_check_config_flags_missing_anthropic_key():
    problems = system_health._check_config(_settings())
    assert any("ANTHROPIC_API_KEY" in p for p in problems)


def test_check_dependency_drift_flags_mismatch(tmp_path, monkeypatch):
    req = tmp_path / "requirements.txt"
    req.write_text("fastapi==0.115.0\nsome-package==9.9.9\n")

    def fake_version(name):
        if name == "fastapi":
            return "0.115.0"
        raise system_health.importlib_metadata.PackageNotFoundError(name)

    monkeypatch.setattr(system_health.importlib_metadata, "version", fake_version)
    problems = system_health._check_dependency_drift(str(req))

    assert any("some-package" in p and "isn't installed" in p for p in problems)
    assert not any("fastapi" in p for p in problems)


def test_check_job_health_flags_failed_agent(db_session):
    from app.models import JobRun, JobRunStatus

    db_session.add(JobRun(job_key="source_leads", status=JobRunStatus.ERROR.value, message="boom"))
    db_session.commit()

    problems = system_health._check_job_health(db_session)
    assert any("Hermes" in p and "boom" in p for p in problems)


def test_run_system_check_creates_item_and_alerts_once(db_session, monkeypatch):
    monkeypatch.setattr(system_health, "_check_config", lambda settings: ["fake problem"])
    monkeypatch.setattr(system_health, "_check_dependency_drift", lambda: [])
    monkeypatch.setattr(system_health, "_check_job_health", lambda session: [])

    alerts = []
    monkeypatch.setattr(system_health, "notify_system_alert", lambda session, settings, subject, body: alerts.append((subject, body)))

    stats_first = system_health.run_system_check(db_session, _settings())
    assert stats_first["alerts_sent"] == 1
    assert len(alerts) == 1
    items = db_session.query(ActionItem).filter(ActionItem.category == "system").all()
    assert len(items) == 1

    stats_second = system_health.run_system_check(db_session, _settings())
    assert stats_second["alerts_sent"] == 0  # same problem, no duplicate alert
    assert len(alerts) == 1
    items_after = db_session.query(ActionItem).filter(ActionItem.category == "system").all()
    assert len(items_after) == 1  # not duplicated


def test_run_system_check_resolves_item_when_problems_clear(db_session, monkeypatch):
    monkeypatch.setattr(system_health, "_check_config", lambda settings: ["fake problem"])
    monkeypatch.setattr(system_health, "_check_dependency_drift", lambda: [])
    monkeypatch.setattr(system_health, "_check_job_health", lambda session: [])
    monkeypatch.setattr(system_health, "notify_system_alert", lambda *a, **k: None)

    system_health.run_system_check(db_session, _settings())

    monkeypatch.setattr(system_health, "_check_config", lambda settings: [])
    system_health.run_system_check(db_session, _settings())

    item = db_session.query(ActionItem).filter(ActionItem.category == "system").one()
    assert item.status == ActionStatus.DONE.value
