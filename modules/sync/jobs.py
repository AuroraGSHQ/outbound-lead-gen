"""Wires the sync module's two sweeps into the shared scheduler.

Wired centrally into app/scheduler.py::start_scheduler() via one call:
    from modules.sync.jobs import register_jobs
    register_jobs(scheduler)
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from modules.sync.service import run_staleness_sweep, sync_emails

logger = logging.getLogger(__name__)


def job_sync_emails() -> None:
    """Job body, matching app/scheduler.py's `_run_safely` convention:
    catch+log so one bad sweep (Gmail down, a bad refresh token) never
    kills the scheduler process. sync_emails() opens its own db_conn()
    internally per message.
    """
    try:
        result = sync_emails()
        logger.info("Job 'sync_emails' finished: %s", result)
    except Exception:  # noqa: BLE001 - a job failing must not crash the scheduler
        logger.exception("Job 'sync_emails' failed")


def job_staleness_sweep() -> None:
    try:
        result = run_staleness_sweep()
        logger.info("Job 'sync_staleness_sweep' finished: %s", result)
    except Exception:  # noqa: BLE001
        logger.exception("Job 'sync_staleness_sweep' failed")


def register_jobs(scheduler: BackgroundScheduler) -> None:
    # Email sync: every 15 minutes, both inboxes.
    scheduler.add_job(
        job_sync_emails,
        CronTrigger(minute="*/15"),
        id="sync_emails",
        replace_existing=True,
    )
    # Staleness / re-enrichment sweep: weekly, Monday 9am UTC.
    scheduler.add_job(
        job_staleness_sweep,
        CronTrigger(day_of_week="mon", hour=9, minute=0),
        id="sync_staleness_sweep",
        replace_existing=True,
    )
