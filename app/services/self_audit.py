"""Prism — self-audit. The manual's sales pitch leans on "we use our own
product" — that has to stay true. Runs the same passive checks Spectrum
(scanner) runs on prospects, but against Aurora's own site (OWN_DOMAIN in
.env), and never creates a ScanResult or feeds Spectrum's prospect list —
purely a truth check on ourselves.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.site_checks import check_site
from app.models import ActionCategory, ActionItem
from app.services import actions

logger = logging.getLogger(__name__)


def run_self_audit(session: Session, settings: Settings) -> dict[str, int]:
    stats = {"faults_found": 0}
    if not settings.own_domain:
        logger.info("OWN_DOMAIN not set — Prism has nothing to check.")
        return stats

    check = check_site(settings.own_domain)
    faults: list[str] = []
    if not check.reachable:
        faults.append(f"unreachable ({check.error or 'no response'})")
    else:
        if check.load_time_ms and check.load_time_ms > 3000:
            faults.append(f"slow page load ({check.load_time_ms}ms)")
        if check.mobile_ok is False:
            faults.append("no mobile viewport tag")
        if check.tracking_present is False:
            faults.append("no conversion tracking detected")
        if check.click_to_call_present is False:
            faults.append("no click-to-call link found")

    if not faults:
        return stats

    title = f"Self-audit: {settings.own_domain} has {len(faults)} issue(s)"
    if session.query(ActionItem).filter(ActionItem.title == title, ActionItem.status != "done").first():
        return stats

    actions.create_action_item(
        session,
        title=title,
        description="; ".join(faults) + " — the same faults we'd flag on a prospect.",
        category=ActionCategory.SYSTEM.value,
        created_by="agent:prism",
    )
    stats["faults_found"] = len(faults)
    logger.info("Self-audit complete: %s", stats)
    return stats
