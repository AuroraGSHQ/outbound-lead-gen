"""Telescope — meeting prep. On-demand only (there's no calendar integration
telling it when a call is coming up): a salesperson triggers it before a
call and gets a one-pager back. Reuses Spectrum's passive site checks for its
own internal prep purposes only — this never creates a ScanResult and never
appears in Spectrum's prospect list, so the two can't get confused for each
other.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.claude import BusinessProfile, ClaudeDrafter
from app.integrations.site_checks import check_site
from app.models import ActionCategory, ActionItem, Lead
from app.services import actions


def _business_profile(settings: Settings) -> BusinessProfile:
    return BusinessProfile(
        business_name=settings.business_name,
        business_pitch=settings.business_pitch,
        sender_name=settings.sender_name,
        sender_title=settings.sender_title,
        calendly_link=settings.calendly_booking_link,
        physical_address=settings.business_physical_address,
    )


def prep_meeting_brief(session: Session, settings: Settings, lead_id: int) -> ActionItem:
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise ValueError(f"No lead {lead_id}")

    findings = "no domain on file for this lead"
    if lead.domain:
        check = check_site(lead.domain)
        parts = []
        if not check.reachable:
            parts.append(f"site unreachable ({check.error or 'no response'})")
        else:
            if check.load_time_ms:
                parts.append(f"load time {check.load_time_ms}ms")
            parts.append(f"mobile-friendly: {check.mobile_ok}")
            parts.append(f"tracking installed: {check.tracking_present}")
            parts.append(f"click-to-call present: {check.click_to_call_present}")
        findings = "; ".join(parts)

    profile = _business_profile(settings)
    drafter = ClaudeDrafter(settings.anthropic_api_key, settings.anthropic_model)
    brief = drafter.draft_meeting_brief(
        {
            "contact_name": lead.contact_name,
            "contact_title": lead.contact_title,
            "company_name": lead.company_name,
            "industry": lead.industry,
            "location": lead.location,
        },
        findings,
        profile,
    )

    item = actions.create_action_item(
        session,
        title=f"Meeting brief — {lead.company_name}",
        category=ActionCategory.ADMIN.value,
        lead_id=lead.id,
        created_by="agent:telescope",
    )
    item.result = brief
    session.commit()
    return item
