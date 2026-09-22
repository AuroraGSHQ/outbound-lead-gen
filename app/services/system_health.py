"""Core — system health. Not part of the sales/marketing pipeline at
all: this watches the machine itself. It checks that required configuration
is present, that every scheduled agent's last run actually succeeded (not
just that it's scheduled — see JobRun, written by scheduler._run_safely),
and that installed packages haven't drifted from what requirements.txt
pins — then alerts immediately (notify_system_alert) rather than waiting
for the daily digest, because "something's broken" shouldn't sit in a queue.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import ActionCategory, ActionItem, ActionStatus, JobRun, JobRunStatus
from app.services import actions
from app.services.notify import notify_system_alert

logger = logging.getLogger(__name__)


def _check_config(settings: Settings) -> list[str]:
    problems = list(settings.require_for_sending())
    if not settings.apollo_api_key:
        problems.append("APOLLO_API_KEY is not set — Voyager (sourcing) can't run")
    if not settings.calendly_webhook_signing_key:
        problems.append("CALENDLY_WEBHOOK_SIGNING_KEY is not set — booked meetings won't be recorded")
    return problems


def _check_dependency_drift(requirements_path: str = "requirements.txt") -> list[str]:
    problems: list[str] = []
    path = Path(requirements_path)
    if not path.exists():
        return problems
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        name, _, pinned = line.partition("==")
        name = name.split("[")[0].strip()
        pinned = pinned.strip()
        try:
            installed = importlib_metadata.version(name)
        except importlib_metadata.PackageNotFoundError:
            problems.append(f"{name} is pinned to {pinned} but isn't installed")
            continue
        if installed != pinned:
            problems.append(f"{name} pinned to {pinned} but {installed} is installed")
    return problems


def _check_job_health(session: Session) -> list[str]:
    from app.scheduler import AGENT_JOBS  # deferred: scheduler.py is this function's caller

    problems: list[str] = []
    for agent in AGENT_JOBS:
        job_id = agent.get("job_id")
        if not job_id:
            continue
        last_run = (
            session.query(JobRun).filter(JobRun.job_key == job_id).order_by(JobRun.started_at.desc()).first()
        )
        if last_run is None or last_run.status != JobRunStatus.ERROR.value:
            continue
        problems.append(f"{agent['name']} ({job_id}) failed on its last run: {last_run.message[:200]}")
    return problems


def run_system_check(session: Session, settings: Settings) -> dict[str, int]:
    problems: list[str] = []
    problems += [f"Config: {p}" for p in _check_config(settings)]
    problems += [f"Dependency drift: {p}" for p in _check_dependency_drift()]
    problems += [f"Agent failure: {p}" for p in _check_job_health(session)]

    stats = {"problems_found": len(problems), "alerts_sent": 0}
    title = "Core: system health issues found"
    existing = (
        session.query(ActionItem)
        .filter(ActionItem.title == title, ActionItem.status != ActionStatus.DONE.value)
        .first()
    )

    if not problems:
        if existing is not None:
            existing.status = ActionStatus.DONE.value
            existing.result = f"Resolved as of {datetime.now(timezone.utc).isoformat()}"
            session.commit()
        return stats

    body = "\n".join(f"- {p}" for p in problems)
    if existing is not None:
        if existing.description == body:
            return stats  # same problems already flagged and already alerted on — don't re-notify
        existing.description = body
        existing.result = f"Updated {datetime.now(timezone.utc).isoformat()}"
        session.commit()
    else:
        actions.create_action_item(
            session,
            title=title,
            description=body,
            category=ActionCategory.SYSTEM.value,
            created_by="agent:core",
        )

    notify_system_alert(session, settings, title, body)
    stats["alerts_sent"] = 1
    logger.info("System health check complete: %s", stats)
    return stats
