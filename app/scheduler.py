"""Wires up the recurring jobs — the actual "24/7" part of the AI-employee
framing. Each job opens its own DB session so failures in one don't corrupt
state for the others, and logs+swallows exceptions so a single bad run
doesn't kill the whole scheduler process.

AGENT_JOBS is the roster the /team page reads to show what's automated, at
what cadence, and (via get_scheduler()) when each one last/next ran.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings
from app.db import SessionLocal
from app.services import ads, conversation, content, metrics, notify, outreach, referrals, scanner, sourcing

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def get_scheduler() -> BackgroundScheduler | None:
    return _scheduler


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


def job_run_scanner_sweep() -> None:
    settings = get_settings()
    _run_safely("run_scanner_sweep", lambda s: scanner.run_scanner_sweep(s, settings))


def job_check_referral_reviews_due() -> None:
    settings = get_settings()
    _run_safely("check_referral_reviews_due", lambda s: referrals.check_referral_reviews_due(s, settings))


def job_generate_ads_brief() -> None:
    settings = get_settings()
    _run_safely("generate_ads_brief", lambda s: ads.generate_monthly_brief(s, settings))


def job_generate_content_extract() -> None:
    settings = get_settings()
    _run_safely("generate_content_extract", lambda s: content.generate_quarterly_extract(s, settings))


def job_compute_metrics_snapshot() -> None:
    _run_safely("compute_metrics_snapshot", lambda s: metrics.compute_and_store_snapshot(s))


# The "AI employee" roster shown on /team — each row maps a named agent to
# the real scheduled job(s) behind it, so the automation is legible rather
# than a marketing claim.
AGENT_JOBS = [
    {
        "key": "scout",
        "name": "Scout",
        "role": "Sourcing",
        "description": (
            "Searches Apollo against your ICP and scores new candidates daily. For Vibe "
            "Prospecting (Explorium) — richer targeting, but credit-metered and confirm-"
            "before-export by design — a person runs the search from a Sourcing request "
            "and imports the CSV; see the Sourcing page."
        ),
        "job_id": "source_leads",
    },
    {
        "key": "scanner",
        "name": "Scanner",
        "role": "Broken-Funnel Scanner (manual §8)",
        "description": "Passively checks queued prospect sites (speed, mobile, tracking, click-to-call) weekly and flags what needs a human's eyes.",
        "job_id": "run_scanner_sweep",
    },
    {
        "key": "scribe",
        "name": "Scribe",
        "role": "Outreach + conversation (manual §9)",
        "description": "Drafts first-touch and follow-up emails, polls for replies, classifies intent — everything queues for approval.",
        "job_id": "generate_drafts",
    },
    {
        "key": "concierge",
        "name": "Concierge",
        "role": "Client intake",
        "description": "On-demand: turns a discovery-call note dump into a structured client record and an action plan.",
        "job_id": None,
    },
    {
        "key": "connector",
        "name": "Connector",
        "role": "Referral engine (manual §11)",
        "description": "Watches for 90-day-review dates and drafts the ask script + forwardable intro message.",
        "job_id": "check_referral_reviews_due",
    },
    {
        "key": "promoter",
        "name": "Promoter",
        "role": "Ads planner (manual §4/§6/§14)",
        "description": "Generates the monthly campaign brief (budget, audience, ad copy) from the budget calendar. Publishing stays manual.",
        "job_id": "generate_ads_brief",
    },
    {
        "key": "analyst",
        "name": "Analyst",
        "role": "Measurement + content (manual §12/§15)",
        "description": "Computes the six KPIs daily and drafts the quarterly benchmark-report extract.",
        "job_id": "compute_metrics_snapshot",
    },
    {
        "key": "courier",
        "name": "Courier",
        "role": "Notifications",
        "description": "Sends the daily owner/team digest and immediate pings for booked meetings.",
        "job_id": "send_digest",
    },
]


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(job_source_leads, CronTrigger(hour=7, minute=0), id="source_leads")
    scheduler.add_job(job_generate_drafts, CronTrigger(hour=7, minute=30), id="generate_drafts")
    scheduler.add_job(job_generate_followups, CronTrigger(hour=8, minute=0), id="generate_followups")
    scheduler.add_job(job_poll_replies, CronTrigger(minute="*/10"), id="poll_replies")
    scheduler.add_job(job_send_digest, CronTrigger(hour=8, minute=30), id="send_digest")
    scheduler.add_job(
        job_run_scanner_sweep, CronTrigger(day_of_week="mon", hour=6, minute=0), id="run_scanner_sweep"
    )
    scheduler.add_job(
        job_check_referral_reviews_due, CronTrigger(hour=6, minute=30), id="check_referral_reviews_due"
    )
    scheduler.add_job(
        job_generate_ads_brief, CronTrigger(day=1, hour=6, minute=0), id="generate_ads_brief"
    )
    scheduler.add_job(
        job_generate_content_extract,
        CronTrigger(month="1,4,7,10", day=1, hour=6, minute=0),
        id="generate_content_extract",
    )
    scheduler.add_job(
        job_compute_metrics_snapshot, CronTrigger(hour=5, minute=0), id="compute_metrics_snapshot"
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info("Scheduler started with jobs: %s", [j.id for j in scheduler.get_jobs()])
    return scheduler
