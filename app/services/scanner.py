"""The Spectrum agent (manual §8): passive site checks + scoring, plus the
human-verification step the manual insists on before any fault gets used in
an outreach draft ("never send a fault-led message you have not personally
verified").
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.claude import BusinessProfile, ClaudeDrafter
from app.integrations.site_checks import check_site
from app.models import (
    ActionCategory,
    ActionItem,
    ActionStatus,
    Conversation,
    Lead,
    LeadStatus,
    Message,
    MessageDirection,
    MessageStatus,
    ScanResult,
)
from app.services.approvals import compose_footer

logger = logging.getLogger(__name__)


def _clean_domain(domain: str) -> str:
    return domain.strip().lower().replace("https://", "").replace("http://", "").rstrip("/")


def queue_target(
    session: Session, *, domain: str, company_name: str = "", added_by_user_id: int | None = None
) -> ScanResult:
    result = ScanResult(
        domain=_clean_domain(domain), company_name=company_name, added_by_user_id=added_by_user_id
    )
    session.add(result)
    session.commit()
    return result


def run_scan(session: Session, scan_result_id: int) -> ScanResult:
    scan = session.get(ScanResult, scan_result_id)
    if scan is None:
        raise ValueError(f"No scan result {scan_result_id}")

    check = check_site(scan.domain)
    faults: list[str] = []

    if not check.reachable:
        faults.append(f"Site unreachable: {check.error or 'no response'}")
    else:
        if check.load_time_ms and check.load_time_ms > 3000:
            faults.append(f"Slow page load ({check.load_time_ms}ms)")
        if check.mobile_ok is False:
            faults.append("No mobile viewport tag — likely not mobile-optimized")
        if check.tracking_present is False:
            faults.append("No conversion tracking detected (no GA/Meta pixel/Google Ads tag found)")
        if check.click_to_call_present is False:
            faults.append("No click-to-call phone link found")
        if check.has_contact_form is False:
            faults.append("No contact form detected on the page")

    from datetime import datetime, timezone

    scan.checked_at = datetime.now(timezone.utc)
    scan.scanned = True
    scan.load_time_ms = check.load_time_ms
    scan.mobile_ok = check.mobile_ok
    scan.tracking_present = check.tracking_present
    scan.click_to_call_present = check.click_to_call_present
    scan.fault_list = faults
    scan.leak_score = round(min(len(faults), 5) / 5, 2)
    scan.verified = False
    scan.verified_by_user_id = None
    session.commit()

    _create_verification_task(session, scan)
    return scan


def run_scanner_sweep(session: Session, settings: Settings) -> dict[str, int]:
    """Scheduled job: (re)scan every target that's never been scanned, or
    hasn't been refreshed in 30 days — manual §8's 'refresh the list monthly'."""
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    targets = (
        session.query(ScanResult)
        .filter((ScanResult.scanned.is_(False)) | (ScanResult.checked_at < cutoff))
        .all()
    )
    stats = {"scanned": 0, "errors": 0}
    for target in targets:
        try:
            run_scan(session, target.id)
            stats["scanned"] += 1
        except Exception:  # noqa: BLE001 - one bad domain shouldn't kill the sweep
            logger.exception("Scan failed for %s", target.domain)
            stats["errors"] += 1
    logger.info("Scanner sweep complete: %s", stats)
    return stats


def _create_verification_task(session: Session, scan: ScanResult) -> ActionItem:
    item = ActionItem(
        title=f"Verify {scan.domain} by hand before using it in outreach",
        description=(
            "Automated checks found: "
            + ("; ".join(scan.fault_list) if scan.fault_list else "no automated faults")
            + ". Manual §8 requires two checks done by a person, once, before this feeds "
            "outreach: submit one genuine test enquiry through their contact form and time "
            "the reply, and call at three different times of day logging whether a human "
            "answers. Tick 'verified' on this scan once that's done."
        ),
        category=ActionCategory.SCANNER_VERIFY.value,
        auto_executable=False,
        status=ActionStatus.PROPOSED.value,
        created_by="agent:spectrum",
        payload={"scan_result_id": scan.id, "domain": scan.domain},
    )
    session.add(item)
    session.commit()
    return item


def mark_verified(
    session: Session, scan_result_id: int, user_id: int, notes: str = ""
) -> ScanResult:
    scan = session.get(ScanResult, scan_result_id)
    if scan is None:
        raise ValueError(f"No scan result {scan_result_id}")
    scan.verified = True
    scan.verified_by_user_id = user_id
    scan.verification_notes = notes
    session.commit()
    return scan


def draft_fault_led_outreach(
    session: Session,
    settings: Settings,
    scan_result_id: int,
    *,
    contact_name: str = "",
    contact_email: str = "",
    contact_title: str = "",
) -> Message:
    """Only callable on a scan a human has ticked verified=True. Creates a
    Lead (source=scanner) if needed and a first-touch draft in the same
    PENDING_APPROVAL queue every other outbound message goes through."""
    scan = session.get(ScanResult, scan_result_id)
    if scan is None:
        raise ValueError(f"No scan result {scan_result_id}")
    if not scan.verified:
        raise ValueError("Cannot draft outreach from an unverified scan result — see manual §8.")
    if not contact_email:
        raise ValueError("A contact email is required to draft outreach.")

    email = contact_email.lower().strip()
    lead = session.query(Lead).filter(Lead.contact_email == email).first()
    if lead is None:
        lead = Lead(
            company_name=scan.company_name or scan.domain,
            domain=scan.domain,
            contact_name=contact_name,
            contact_title=contact_title,
            contact_email=email,
            source="scanner",
            status=LeadStatus.QUEUED.value,
            notes=f"Sourced via broken-funnel scan of {scan.domain}.",
        )
        session.add(lead)
        session.flush()
    scan.lead_id = lead.id
    session.commit()

    profile = BusinessProfile(
        business_name=settings.business_name,
        business_pitch=settings.business_pitch,
        sender_name=settings.sender_name,
        sender_title=settings.sender_title,
        calendly_link=settings.calendly_booking_link,
        physical_address=settings.business_physical_address,
    )
    drafter = ClaudeDrafter(settings.anthropic_api_key, settings.anthropic_model)
    draft = drafter.draft_fault_led_email(
        {
            "contact_name": lead.contact_name,
            "company_name": lead.company_name,
            "industry": lead.industry,
            "location": lead.location,
        },
        scan.fault_list or [],
        profile,
    )
    greeting = f"Hi {lead.contact_name.split(' ')[0]}," if lead.contact_name else "Hi,"
    body = (
        f"{greeting}\n\n{draft['body']}\n\nBest,\n{profile.sender_name}\n{profile.sender_title}"
        f"\n\n{compose_footer(profile)}"
    )

    conversation = Conversation(lead_id=lead.id, gmail_thread_id="", autopilot=False)
    session.add(conversation)
    session.flush()
    message = Message(
        conversation_id=conversation.id,
        direction=MessageDirection.OUTBOUND.value,
        subject=draft.get("subject") or f"your site, {scan.domain}",
        body=body,
        status=MessageStatus.PENDING_APPROVAL.value,
    )
    session.add(message)
    session.commit()
    return message


def list_scan_results(session: Session, *, unverified_only: bool = False) -> list[ScanResult]:
    query = session.query(ScanResult)
    if unverified_only:
        query = query.filter(ScanResult.verified.is_(False))
    return query.order_by(ScanResult.checked_at.desc()).all()
