"""Registers the dialer's recurring job. Wired centrally into
app/scheduler.py::start_scheduler() via register_jobs(scheduler).
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings
from modules.dialer.service import run_dial_window

logger = logging.getLogger(__name__)


def job_run_dial_window() -> None:
    """Job body: opens its own settings/DB access (via service.run_dial_window
    -> modules.common.db.db_conn()), never shares state with other jobs.
    Wrapped in try/except so one bad run doesn't kill the scheduler, matching
    the _run_safely pattern in app/scheduler.py.
    """
    settings = get_settings()
    try:
        result = run_dial_window(settings)
        logger.info("Job 'dialer_run_dial_window' finished: %s", result)
    except Exception:  # noqa: BLE001 - a job failing must not crash the scheduler
        logger.exception("Job 'dialer_run_dial_window' failed")


def register_jobs(scheduler: BackgroundScheduler) -> None:
    """Runs every 20 minutes; the actual business-hours gating happens inside
    run_dial_window() itself, so this can run on a tight cron safely.
    """
    scheduler.add_job(
        job_run_dial_window,
        CronTrigger(minute="*/20"),
        id="dialer_run_dial_window",
        replace_existing=True,
    )
