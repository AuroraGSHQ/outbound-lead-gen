"""Wires the notifications delivery sweep into the shared scheduler.

Wired centrally into app/scheduler.py::start_scheduler() via one call:
    from modules.notifications.jobs import register_jobs
    register_jobs(scheduler)
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from modules.notifications.service import deliver_pending_notifications

logger = logging.getLogger(__name__)


def job_deliver_pending_notifications() -> None:
    """Job body, matching the app/scheduler.py::_run_safely convention:
    catch+log so one bad sweep (e.g. Twilio down) never kills the scheduler
    process. deliver_pending_notifications() opens its own db_conn()
    internally and already records per-row failures on the row itself, so
    this try/except is purely a last-resort net around anything unexpected
    (e.g. the DB itself being unreachable).
    """
    try:
        result = deliver_pending_notifications()
        logger.info("Job 'deliver_pending_notifications' finished: %s", result)
    except Exception:  # noqa: BLE001 - a job failing must not crash the scheduler
        logger.exception("Job 'deliver_pending_notifications' failed")


def register_jobs(scheduler: BackgroundScheduler) -> None:
    """Registers the notification delivery sweep on a short (3 minute)
    cadence — this is the "make it feel like Jarvis" latency: tight enough
    that a payment/deadline/contract ping reaches your phone within a few
    minutes, not so tight it hammers the Twilio API for no reason.
    """
    scheduler.add_job(
        job_deliver_pending_notifications,
        IntervalTrigger(minutes=3),
        id="deliver_pending_notifications",
        replace_existing=True,
    )
