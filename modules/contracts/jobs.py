"""Scheduled jobs for the contracts module.

Wired centrally into app/scheduler.py::start_scheduler() via
`register_jobs(scheduler)`, per the modules/README.md integration contract.

Contracts are mostly event-driven (approve -> send, provider webhook ->
signed), so the only recurring job here is a safety-net poll: in case a
provider webhook gets missed/dropped, periodically re-check any contract
still sitting in 'sent' against the provider's own status API.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from modules.contracts.service import poll_sent_contracts_for_signature

logger = logging.getLogger(__name__)


def _run_safely(name: str, fn) -> None:
    try:
        fn()
        logger.info("Job '%s' finished", name)
    except Exception:  # noqa: BLE001 - a job failing must not crash the scheduler
        logger.exception("Job '%s' failed", name)


def job_poll_signed_contracts() -> None:
    _run_safely("contracts_poll_signed", poll_sent_contracts_for_signature)


def register_jobs(scheduler: BackgroundScheduler) -> None:
    scheduler.add_job(
        job_poll_signed_contracts,
        CronTrigger(minute=0),  # hourly, on the hour
        id="contracts_poll_signed",
    )
