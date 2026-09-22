"""Wires up the recurring jobs — the actual "24/7" part of the AI-employee
framing. Every job records a JobRun (ok/error/skipped) so Core can
tell a failing agent from a quiet one, and every job checks
agent_toggles.is_enabled() for its owning agent before doing anything —
that's the single place the on/off switch (Team page) actually takes
effect. Each job opens its own DB session so failures in one don't corrupt
state for the others, and swallows exceptions so a single bad run doesn't
kill the whole scheduler process.

AGENT_JOBS is the roster the /team page reads: name, astronomy-themed role,
description, the scheduled job that backs it (if any), and the toggle
`key`. Several jobs can belong to one agent (Beacon/Scribe runs three); the
toggle always applies at the agent level, not the individual job.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings
from app.db import SessionLocal
from app.models import JobRun, JobRunStatus
from app.services import (
    agent_toggles,
    ads,
    billing,
    case_studies,
    churn_watch,
    content,
    conversation,
    creative_refresh,
    deliverability,
    metrics,
    notify,
    onboarding,
    outreach,
    ops_rollup,
    referrals,
    reviews,
    scanner,
    self_audit,
    sourcing,
    system_health,
    winback,
)
# crm_sync (Wormhole) is on-demand only — no scheduled job — so it is not
# imported here; see AGENT_JOBS below for its roster entry.

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def get_scheduler() -> BackgroundScheduler | None:
    return _scheduler


def _run_safely(job_key: str, agent_key: str, fn) -> None:
    session = SessionLocal()
    started = datetime.now(timezone.utc)
    try:
        if not agent_toggles.is_enabled(session, agent_key):
            logger.info("Job '%s' skipped — agent '%s' is turned off", job_key, agent_key)
            session.add(
                JobRun(
                    job_key=job_key,
                    status=JobRunStatus.SKIPPED_DISABLED.value,
                    message=f"agent '{agent_key}' is disabled",
                    started_at=started,
                    finished_at=datetime.now(timezone.utc),
                )
            )
            session.commit()
            return

        result = fn(session)
        session.add(
            JobRun(
                job_key=job_key,
                status=JobRunStatus.OK.value,
                message=str(result)[:1000],
                started_at=started,
                finished_at=datetime.now(timezone.utc),
            )
        )
        session.commit()
        logger.info("Job '%s' finished: %s", job_key, result)
    except Exception as exc:  # noqa: BLE001 - a job failing must not crash the scheduler
        logger.exception("Job '%s' failed", job_key)
        try:
            session.rollback()
            session.add(
                JobRun(
                    job_key=job_key,
                    status=JobRunStatus.ERROR.value,
                    message=str(exc)[:1000],
                    started_at=started,
                    finished_at=datetime.now(timezone.utc),
                )
            )
            session.commit()
        except Exception:  # noqa: BLE001 - recording the failure must not itself crash anything
            logger.exception("Failed to record JobRun for '%s'", job_key)
    finally:
        session.close()


# --- Job functions, one per scheduled cron entry ---------------------------


def job_source_leads() -> None:
    settings = get_settings()
    _run_safely("source_leads", "voyager", lambda s: sourcing.run_sourcing_sweep(s, settings))


def job_generate_drafts() -> None:
    settings = get_settings()
    _run_safely("generate_drafts", "beacon", lambda s: outreach.generate_pending_drafts(s, settings))


def job_generate_followups() -> None:
    settings = get_settings()
    _run_safely(
        "generate_followups", "beacon", lambda s: outreach.generate_followups_for_stale_conversations(s, settings)
    )


def job_poll_replies() -> None:
    settings = get_settings()
    _run_safely("poll_replies", "beacon", lambda s: conversation.poll_and_process_replies(s, settings))


def job_send_digest() -> None:
    settings = get_settings()
    _run_safely("send_digest", "relay", lambda s: notify.send_owner_digest(s, settings))


def job_run_scanner_sweep() -> None:
    settings = get_settings()
    _run_safely("run_scanner_sweep", "spectrum", lambda s: scanner.run_scanner_sweep(s, settings))


def job_check_referral_reviews_due() -> None:
    settings = get_settings()
    _run_safely("check_referral_reviews_due", "constellation", lambda s: referrals.check_referral_reviews_due(s, settings))


def job_generate_ads_brief() -> None:
    settings = get_settings()
    _run_safely("generate_ads_brief", "orbit", lambda s: ads.generate_monthly_brief(s, settings))


def job_generate_content_extract() -> None:
    settings = get_settings()
    _run_safely("generate_content_extract", "observatory", lambda s: content.generate_quarterly_extract(s, settings))


def job_compute_metrics_snapshot() -> None:
    _run_safely("compute_metrics_snapshot", "observatory", lambda s: metrics.compute_and_store_snapshot(s))


def job_run_onboarding_sweep() -> None:
    settings = get_settings()
    _run_safely("run_onboarding_sweep", "launchpad", lambda s: onboarding.run_onboarding_sweep(s, settings))


def job_run_billing_sweep() -> None:
    settings = get_settings()
    _run_safely("run_billing_sweep", "equinox", lambda s: billing.run_billing_sweep(s, settings))


def job_check_review_requests_due() -> None:
    settings = get_settings()
    _run_safely("check_review_requests_due", "radiance", lambda s: reviews.check_review_requests_due(s, settings))


def job_check_case_study_candidates() -> None:
    settings = get_settings()
    _run_safely("check_case_study_candidates", "zenith", lambda s: case_studies.check_case_study_candidates(s, settings))


def job_check_deliverability() -> None:
    settings = get_settings()
    _run_safely("check_deliverability", "sentinel", lambda s: deliverability.check_deliverability(s, settings))


def job_check_winback_due() -> None:
    settings = get_settings()
    _run_safely("check_winback_due", "gravity", lambda s: winback.check_winback_due(s, settings))


def job_run_self_audit() -> None:
    settings = get_settings()
    _run_safely("run_self_audit", "prism", lambda s: self_audit.run_self_audit(s, settings))


def job_check_creative_fatigue() -> None:
    settings = get_settings()
    _run_safely("check_creative_fatigue", "nova", lambda s: creative_refresh.check_creative_fatigue(s, settings))


def job_check_churn_risk() -> None:
    settings = get_settings()
    _run_safely("check_churn_risk", "collision", lambda s: churn_watch.check_churn_risk(s, settings))


def job_send_monthly_rollup() -> None:
    settings = get_settings()
    _run_safely("send_monthly_rollup", "epoch", lambda s: ops_rollup.send_monthly_rollup(s, settings))


def job_system_health_check() -> None:
    settings = get_settings()
    _run_safely("system_health_check", "core", lambda s: system_health.run_system_check(s, settings))


JOB_FUNCTIONS = {
    "source_leads": job_source_leads,
    "generate_drafts": job_generate_drafts,
    "generate_followups": job_generate_followups,
    "poll_replies": job_poll_replies,
    "send_digest": job_send_digest,
    "run_scanner_sweep": job_run_scanner_sweep,
    "check_referral_reviews_due": job_check_referral_reviews_due,
    "generate_ads_brief": job_generate_ads_brief,
    "generate_content_extract": job_generate_content_extract,
    "compute_metrics_snapshot": job_compute_metrics_snapshot,
    "run_onboarding_sweep": job_run_onboarding_sweep,
    "run_billing_sweep": job_run_billing_sweep,
    "check_review_requests_due": job_check_review_requests_due,
    "check_case_study_candidates": job_check_case_study_candidates,
    "check_deliverability": job_check_deliverability,
    "check_winback_due": job_check_winback_due,
    "run_self_audit": job_run_self_audit,
    "check_creative_fatigue": job_check_creative_fatigue,
    "check_churn_risk": job_check_churn_risk,
    "send_monthly_rollup": job_send_monthly_rollup,
    "system_health_check": job_system_health_check,
}


# The "AI employee" roster shown on /team — each row maps a named agent to
# the real scheduled job(s) behind it. `key` is what agent_toggles keys off
# of; `job_id` is the representative scheduler job shown for "next run"
# (an agent that backs several jobs, like Beacon, shows just one). Every
# agent is named after something in the sky — the company is the universe,
# so the naming pool is effectively unlimited, unlike a finite pantheon.
AGENT_JOBS = [
    {
        "key": "voyager",
        "name": "Voyager",
        "role": "Sourcing",
        "description": (
            "Searches Apollo.io against your ICP and scores new candidates daily. For Vibe "
            "Prospecting (Explorium) — richer targeting, but credit-metered and confirm-"
            "before-export by design — a person runs the search from a Sourcing request "
            "and imports the CSV; see the Sourcing page."
        ),
        "job_id": "source_leads",
    },
    {
        "key": "spectrum",
        "name": "Spectrum",
        "role": "Broken-Funnel Scanner (manual §8)",
        "description": "Passively checks queued prospect sites (speed, mobile, tracking, click-to-call) weekly and flags what needs a human's eyes.",
        "job_id": "run_scanner_sweep",
    },
    {
        "key": "beacon",
        "name": "Beacon",
        "role": "Outreach + conversation (manual §9)",
        "description": "Drafts first-touch and follow-up emails, polls for replies, classifies intent. Also drafts SMS and one-way voice messages (Twilio + ElevenLabs) on demand from a lead's page once they have a phone number on file — everything queues for approval.",
        "job_id": "generate_drafts",
    },
    {
        "key": "horizon",
        "name": "Horizon",
        "role": "Client intake",
        "description": "On-demand: turns a discovery-call note dump into a structured client record and an action plan.",
        "job_id": None,
    },
    {
        "key": "constellation",
        "name": "Constellation",
        "role": "Referral engine (manual §11)",
        "description": "Watches for 90-day-review dates and drafts the ask script + forwardable intro message.",
        "job_id": "check_referral_reviews_due",
    },
    {
        "key": "orbit",
        "name": "Orbit",
        "role": "Ads planner (manual §4/§6/§14)",
        "description": "Generates the monthly campaign brief (budget, audience, ad copy) from the budget calendar. Publishing stays manual.",
        "job_id": "generate_ads_brief",
    },
    {
        "key": "observatory",
        "name": "Observatory",
        "role": "Measurement + content (manual §12/§15)",
        "description": "Computes the six KPIs daily and drafts the quarterly benchmark-report extract.",
        "job_id": "compute_metrics_snapshot",
    },
    {
        "key": "relay",
        "name": "Relay",
        "role": "Notifications",
        "description": "Sends the daily owner/team digest and immediate pings for booked meetings.",
        "job_id": "send_digest",
    },
    {
        "key": "launchpad",
        "name": "Launchpad",
        "role": "Client onboarding & delivery",
        "description": "Plants the onboarding checklist the moment a client goes active — kickoff, tracking, first campaign, booking-flow test, 30-day check-in.",
        "job_id": "run_onboarding_sweep",
    },
    {
        "key": "equinox",
        "name": "Equinox",
        "role": "Billing & invoicing",
        "description": "Flags each active client's monthly invoice as due, and escalates if last month's is still unconfirmed. No payment processor wired in — this is the reminder layer.",
        "job_id": "run_billing_sweep",
    },
    {
        "key": "radiance",
        "name": "Radiance",
        "role": "Reputation & reviews",
        "description": "Nudges for a review 60 days into a client relationship, and drafts a reply for any review you paste in.",
        "job_id": "check_review_requests_due",
    },
    {
        "key": "zenith",
        "name": "Zenith",
        "role": "Case study builder",
        "description": "Once a client has 90 days of real results, drafts the case study — manual §1's single most persuasive asset.",
        "job_id": "check_case_study_candidates",
    },
    {
        "key": "telescope",
        "name": "Telescope",
        "role": "Meeting prep",
        "description": "On-demand: pulls a prospect's site into a one-page pre-call brief before a discovery call.",
        "job_id": None,
    },
    {
        "key": "axis",
        "name": "Axis",
        "role": "Proposals & contracts",
        "description": "Turns Horizon's intake recommendation into a ready-to-send proposal — scope, price, the booked-job-floor guarantee.",
        "job_id": None,
    },
    {
        "key": "sentinel",
        "name": "Sentinel",
        "role": "Deliverability watch",
        "description": "Watches send-volume trend and Gmail token health — the hard ceiling on how fast outbound can scale.",
        "job_id": "check_deliverability",
    },
    {
        "key": "eclipse",
        "name": "Eclipse",
        "role": "Competitor watch",
        "description": "On-demand: log a competitor's ad, get it run through the manual's own differentiation test.",
        "job_id": None,
    },
    {
        "key": "gravity",
        "name": "Gravity",
        "role": "Win-back",
        "description": "Six weeks after a decline, drafts a low-pressure referral ask — manual §11's most overlooked source.",
        "job_id": "check_winback_due",
    },
    {
        "key": "prism",
        "name": "Prism",
        "role": "Self-audit",
        "description": "Runs Spectrum's own checks against Aurora's own site — 'we use our own product' has to stay true.",
        "job_id": "run_self_audit",
    },
    {
        "key": "nova",
        "name": "Nova",
        "role": "Creative fatigue watch",
        "description": "Flags any ad campaign running past 3-4 weeks, per manual §6 — performance quietly degrades past that.",
        "job_id": "check_creative_fatigue",
    },
    {
        "key": "collision",
        "name": "Collision",
        "role": "Churn early-warning",
        "description": "Flags an active client that's gone quiet for 60+ days, before it shows up as a missed payment.",
        "job_id": "check_churn_risk",
    },
    {
        "key": "parallax",
        "name": "Parallax",
        "role": "Pricing benchmark",
        "description": "On-demand: log a competitor's pricing, get it checked against Aurora's own positioning.",
        "job_id": None,
    },
    {
        "key": "epoch",
        "name": "Epoch",
        "role": "Monthly ops rollup",
        "description": "A once-a-month management view — what's aging on the board, by category. Distinct cadence from Relay's daily digest on purpose.",
        "job_id": "send_monthly_rollup",
    },
    {
        "key": "core",
        "name": "Core",
        "role": "System health",
        "description": "Checks configuration completeness, dependency drift, and whether every other agent's last run actually succeeded — alerts immediately, not just in a digest.",
        "job_id": "system_health_check",
    },
    {
        "key": "wormhole",
        "name": "Wormhole",
        "role": "CRM handoff",
        "description": "On-demand: paste in a lead or client's details and Wormhole extracts the contact fields and pushes them straight into that client's own CRM (HubSpot, Monday.com, GoHighLevel).",
        "job_id": None,
    },
    {
        "key": "pulsar",
        "name": "Pulsar",
        "role": "Social media",
        "description": "On-demand: drafts organic social post copy/captions for a given campaign or recent case study, queued for approval like outreach drafts are.",
        "job_id": None,
    },
    {
        "key": "quasar",
        "name": "Quasar",
        "role": "High-impact campaigns",
        "description": "On-demand: spots a small number of high-leverage campaign opportunities from recent metrics/campaign data and raises them for the team to review — an opportunity spotter, not a full campaign builder.",
        "job_id": None,
    },
    {
        "key": "comet",
        "name": "Comet",
        "role": "Trend detection",
        "description": "On-demand: scans recent metric history for a directional trend (a KPI moving the same way several periods running) and flags it once it crosses a threshold.",
        "job_id": None,
    },
    {
        "key": "nebula",
        "name": "Nebula",
        "role": "Content generation",
        "description": "On-demand: drafts long-form content (a blog post or newsletter section) from a template plus recent case-study or metric data, queued for review.",
        "job_id": None,
    },
    {
        "key": "apollo",
        "name": "Apollo",
        "role": "Production (video/photo)",
        "description": "On-demand: builds a production checklist (shot list, deliverables, deadline) when a campaign needs video/photo content — backs the Content Production capability on the Aurora website.",
        "job_id": None,
    },
    {
        "key": "starlight",
        "name": "Starlight",
        "role": "Brand identity",
        "description": "On-demand: reviews a client's current brand assets/voice for consistency and raises what it finds.",
        "job_id": None,
    },
    {
        "key": "supernova",
        "name": "Supernova",
        "role": "Campaign launch",
        "description": "On-demand: assembles a go-live checklist (assets, targeting, budget confirmation) the moment a campaign is marked ready to publish.",
        "job_id": None,
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
    scheduler.add_job(job_run_onboarding_sweep, CronTrigger(hour=9, minute=0), id="run_onboarding_sweep")
    scheduler.add_job(job_run_billing_sweep, CronTrigger(hour=9, minute=15), id="run_billing_sweep")
    scheduler.add_job(
        job_check_review_requests_due, CronTrigger(hour=9, minute=30), id="check_review_requests_due"
    )
    scheduler.add_job(
        job_check_case_study_candidates,
        CronTrigger(day_of_week="wed", hour=6, minute=0),
        id="check_case_study_candidates",
    )
    scheduler.add_job(job_check_deliverability, CronTrigger(hour="*/2", minute=0), id="check_deliverability")
    scheduler.add_job(job_check_winback_due, CronTrigger(hour=10, minute=0), id="check_winback_due")
    scheduler.add_job(job_run_self_audit, CronTrigger(hour=10, minute=15), id="run_self_audit")
    scheduler.add_job(
        job_check_creative_fatigue, CronTrigger(day_of_week="mon", hour=6, minute=15), id="check_creative_fatigue"
    )
    scheduler.add_job(job_check_churn_risk, CronTrigger(hour=10, minute=30), id="check_churn_risk")
    scheduler.add_job(job_send_monthly_rollup, CronTrigger(day=1, hour=7, minute=0), id="send_monthly_rollup")
    scheduler.add_job(job_system_health_check, CronTrigger(minute="*/30"), id="system_health_check")

    scheduler.start()
    _scheduler = scheduler
    logger.info("Scheduler started with jobs: %s", [j.id for j in scheduler.get_jobs()])
    return scheduler
