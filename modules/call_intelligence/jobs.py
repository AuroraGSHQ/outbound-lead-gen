"""Recurring work for call_intelligence: a transcript-processing sweep and
the daily deadline nag. Wired centrally into app/scheduler.py::start_scheduler()
via register_jobs(scheduler) — per modules/README.md's integration contract.

Each job opens its own modules.common.db.db_conn() and wraps its body in
try/except + logging, matching app/scheduler.py::_run_safely so one bad run
can't kill the whole BackgroundScheduler process.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import text

from modules.call_intelligence.service import process_call, run_daily_deadline_sweep
from modules.common.db import db_conn

logger = logging.getLogger(__name__)


def _run_safely(name: str, fn) -> None:
    try:
        result = fn()
        logger.info("Job '%s' finished: %s", name, result)
    except Exception:  # noqa: BLE001 - a job failing must not crash the scheduler
        logger.exception("Job '%s' failed", name)


def job_process_pending_calls() -> dict:
    """Every ~10 minutes: process any call_logs row that has a transcript
    and hasn't been summarized yet — covers ai_outbound rows the dialer
    module writes, plus any zoom/phone_manual row a webhook left unprocessed
    (e.g. transcript arrived after the initial webhook call).
    """
    with db_conn() as conn:
        rows = conn.execute(
            text(
                "SELECT id FROM call_logs WHERE transcript != '' AND processed_at IS NULL"
            )
        ).mappings().all()
    ids = [row["id"] for row in rows]

    processed = 0
    failed = 0
    for call_log_id in ids:
        try:
            process_call(call_log_id)
            processed += 1
        except Exception:  # noqa: BLE001 - one bad call_log must not stop the sweep
            failed += 1
            logger.exception("job_process_pending_calls: failed to process call_log %s", call_log_id)

    return {"seen": len(ids), "processed": processed, "failed": failed}


def job_deadline_sweep() -> dict:
    return run_daily_deadline_sweep()


def register_jobs(scheduler: BackgroundScheduler) -> None:
    scheduler.add_job(
        lambda: _run_safely("call_intelligence.process_pending_calls", job_process_pending_calls),
        CronTrigger(minute="*/10"),
        id="call_intelligence_process_pending_calls",
    )
    # Existing app jobs run at 07:00/07:30/08:00/08:30 UTC (app/scheduler.py);
    # 08:45 UTC keeps this in the same "morning batch" without colliding.
    scheduler.add_job(
        lambda: _run_safely("call_intelligence.deadline_sweep", job_deadline_sweep),
        CronTrigger(hour=8, minute=45),
        id="call_intelligence_deadline_sweep",
    )
