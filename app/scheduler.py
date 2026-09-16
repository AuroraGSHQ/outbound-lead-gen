"""Wires up the recurring jobs. Each job opens its own DB session so failures
in one don't corrupt state for the others, and logs+swallows exceptions so a
single bad run doesn't kill the whole scheduler process.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings
from app.db import SessionLocal
from app.services import conversation, notify, outreach, sourcing
from modules.call_intelligence.jobs import register_jobs as register_call_intelligence_jobs
from modules.contracts.jobs import register_jobs as register_contracts_jobs
from modules.dialer.jobs import register_jobs as register_dialer_jobs
from modules.notifications.jobs import register_jobs as register_notifications_jobs
from modules.sync.jobs import register_jobs as register_sync_jobs

logger = logging.getLogger(__name__)


def _run_safely(name: str, fn) -> None:
    session = SessionLocal()
    try:
        result = fn(session)
        logger.info("Job '%s' finished: %s", name, result)
    except Exception:  # noqa: BLE001 - a job failing must not crash the scheduler
        logger.exception("Job '%s' failed", name)
    finally:
        session.close()


def job_source_leads() -> None:
    settings = get_settings()
    _run_safely("source_leads", lambda s: sourcing.run_sourcing_sweep(s, settings))


def job_generate_drafts() -> None:
    settings = get_settings()
    _run_safely("generate_drafts", lambda s: outreach.generate_pending_drafts(s, settings))


def job_generate_followups() -> None:
    settings = get_settings()
    _run_safely(
        "generate_followups", lambda s: outreach.generate_followups_for_stale_conversations(s, settings)
    )


def job_poll_replies() -> None:
    settings = get_settings()
    _run_safely("poll_replies", lambda s: conversation.poll_and_process_replies(s, settings))


def job_send_digest() -> None:
    settings = get_settings()
    _run_safely("send_digest", lambda s: notify.send_owner_digest(s, settings))


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(job_source_leads, CronTrigger(hour=7, minute=0), id="source_leads")
    scheduler.add_job(job_generate_drafts, CronTrigger(hour=7, minute=30), id="generate_drafts")
    scheduler.add_job(job_generate_followups, CronTrigger(hour=8, minute=0), id="generate_followups")
    scheduler.add_job(job_poll_replies, CronTrigger(minute="*/10"), id="poll_replies")
    scheduler.add_job(job_send_digest, CronTrigger(hour=8, minute=30), id="send_digest")

    # JARVIS modules — each owns its own job registration; see
    # modules/README.md for the router.py/jobs.py integration contract.
    register_dialer_jobs(scheduler)
    register_call_intelligence_jobs(scheduler)
    register_contracts_jobs(scheduler)
    register_notifications_jobs(scheduler)
    register_sync_jobs(scheduler)

    scheduler.start()
    logger.info("Scheduler started with jobs: %s", [j.id for j in scheduler.get_jobs()])
    return scheduler
