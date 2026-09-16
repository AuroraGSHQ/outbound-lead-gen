"""Unit tests for run_daily_deadline_sweep's reminder-count / cap logic.

No network, no real DB: modules.common.db.db_conn and
modules.common.notifications.create_notification are mocked out with an
in-memory fake connection.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from modules.call_intelligence import service


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class FakeConn:
    """Enough of a SQLAlchemy Connection to satisfy run_daily_deadline_sweep:
    SELECT open deadlines, UPDATE reminded_count/last_reminded_at.
    """

    def __init__(self, deadlines: list[dict]):
        self.deadlines = deadlines

    def execute(self, stmt, params=None):
        sql = str(stmt)
        params = params or {}
        if "SELECT * FROM deadlines" in sql:
            today = params["today"]
            rows = [
                d for d in self.deadlines
                if d["due_date"] <= today and d["completed"] == 0
            ]
            return FakeResult(rows)
        if "UPDATE deadlines SET reminded_count" in sql:
            for d in self.deadlines:
                if d["id"] == params["id"]:
                    d["reminded_count"] += 1
                    d["last_reminded_at"] = params["now"]
            return FakeResult([])
        raise AssertionError(f"Unexpected SQL in test: {sql}")


@contextmanager
def _fake_db_conn(conn):
    yield conn


class FakeSettings:
    deadline_max_reminders = 3


def _run_sweep(deadlines: list[dict]):
    conn = FakeConn(deadlines)
    with patch.object(service, "db_conn", lambda: _fake_db_conn(conn)), \
         patch.object(service, "get_settings", lambda: FakeSettings()), \
         patch.object(service, "create_notification") as mock_notify:
        result = service.run_daily_deadline_sweep()
    return result, mock_notify, deadlines


def _deadline(id_, *, due_date, reminded_count=0, completed=0):
    return {
        "id": id_,
        "call_log_id": None,
        "contact_id": None,
        "description": f"deadline {id_}",
        "due_date": due_date,
        "completed": completed,
        "reminded_count": reminded_count,
        "last_reminded_at": None,
    }


def test_overdue_deadline_under_cap_is_reminded_and_incremented():
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    deadlines = [_deadline(1, due_date=yesterday, reminded_count=0)]

    result, mock_notify, deadlines = _run_sweep(deadlines)

    assert result == {"reminded": 1, "capped_skipped": 0}
    assert deadlines[0]["reminded_count"] == 1
    assert deadlines[0]["last_reminded_at"] is not None
    mock_notify.assert_called_once()
    _, kwargs = mock_notify.call_args
    assert "final reminder" not in kwargs["body"].lower()
    assert "final reminder" not in kwargs["title"].lower()


def test_reminder_that_reaches_cap_is_labeled_final():
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    # max_reminders=3: a deadline already reminded twice, this 3rd reminder
    # reaches the cap and should be labeled "final reminder".
    deadlines = [_deadline(2, due_date=yesterday, reminded_count=2)]

    result, mock_notify, deadlines = _run_sweep(deadlines)

    assert result == {"reminded": 1, "capped_skipped": 0}
    assert deadlines[0]["reminded_count"] == 3
    _, kwargs = mock_notify.call_args
    assert "final reminder" in kwargs["title"].lower() or "final reminder" in kwargs["body"].lower()


def test_deadline_at_cap_is_skipped_and_not_reminded_again():
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    deadlines = [_deadline(3, due_date=yesterday, reminded_count=3)]

    result, mock_notify, deadlines = _run_sweep(deadlines)

    assert result == {"reminded": 0, "capped_skipped": 1}
    assert deadlines[0]["reminded_count"] == 3  # unchanged
    mock_notify.assert_not_called()


def test_completed_deadlines_are_never_reminded():
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    deadlines = [_deadline(4, due_date=yesterday, reminded_count=0, completed=1)]

    result, mock_notify, deadlines = _run_sweep(deadlines)

    assert result == {"reminded": 0, "capped_skipped": 0}
    mock_notify.assert_not_called()


def test_future_deadlines_are_not_touched():
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    deadlines = [_deadline(5, due_date=tomorrow, reminded_count=0)]

    result, mock_notify, deadlines = _run_sweep(deadlines)

    assert result == {"reminded": 0, "capped_skipped": 0}
    mock_notify.assert_not_called()


def test_due_today_counts_as_due():
    today = date.today().isoformat()
    deadlines = [_deadline(6, due_date=today, reminded_count=0)]

    result, mock_notify, deadlines = _run_sweep(deadlines)

    assert result == {"reminded": 1, "capped_skipped": 0}
    mock_notify.assert_called_once()
