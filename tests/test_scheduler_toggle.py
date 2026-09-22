"""_run_safely is the one place the on/off switch actually takes effect and
where every job's outcome gets recorded — so it gets a dedicated,
isolated-DB test rather than relying on the real app.db connection.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import scheduler
from app.models import Base, JobRunStatus
from app.services import agent_toggles


@pytest.fixture()
def isolated_session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(scheduler, "SessionLocal", factory)
    yield factory
    engine.dispose()


def _job_runs(factory, job_key):
    session = factory()
    try:
        return session.query(scheduler.JobRun).filter(scheduler.JobRun.job_key == job_key).all()
    finally:
        session.close()


def test_run_safely_executes_and_records_success(isolated_session_factory):
    calls = []
    scheduler._run_safely("test_job", "horizon", lambda s: calls.append(1) or "ok")

    assert calls == [1]
    runs = _job_runs(isolated_session_factory, "test_job")
    assert len(runs) == 1
    assert runs[0].status == JobRunStatus.OK.value


def test_run_safely_skips_when_agent_disabled(isolated_session_factory):
    session = isolated_session_factory()
    agent_toggles.set_enabled(session, "horizon", False)
    session.close()

    calls = []
    scheduler._run_safely("test_job", "horizon", lambda s: calls.append(1))

    assert calls == []  # never invoked
    runs = _job_runs(isolated_session_factory, "test_job")
    assert len(runs) == 1
    assert runs[0].status == JobRunStatus.SKIPPED_DISABLED.value


def test_run_safely_records_error_and_does_not_raise(isolated_session_factory):
    def _boom(session):
        raise RuntimeError("kaboom")

    scheduler._run_safely("test_job", "horizon", _boom)  # must not raise

    runs = _job_runs(isolated_session_factory, "test_job")
    assert len(runs) == 1
    assert runs[0].status == JobRunStatus.ERROR.value
    assert "kaboom" in runs[0].message
