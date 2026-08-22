"""Keep the business owner in the loop: a daily digest, plus immediate pings
for things that shouldn't wait (a booked meeting, a hard 'not interested').
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations import gmail
from app.models import DigestState, Lead, LeadStatus, Meeting, Message, MessageStatus

logger = logging.getLogger(__name__)


def _send_owner_email(settings: Settings, subject: str, body: str) -> None:
    if not settings.owner_email:
        logger.warning("OWNER_EMAIL not set; skipping notification: %s", subject)
        return
    service = gmail.build_service(settings.gmail_token_path)
    gmail.send_email(
        service,
        sender=settings.gmail_sender_email,
        to=settings.owner_email,
        subject=subject,
        body=body,
    )


def notify_owner_now(settings: Settings, subject: str, body: str) -> None:
    _send_owner_email(settings, subject, body)


def notify_meeting_booked(settings: Settings, lead: Lead, meeting: Meeting) -> None:
    when = meeting.scheduled_time.isoformat() if meeting.scheduled_time else "an unspecified time"
    body = (
        f"{lead.contact_name} ({lead.contact_title} at {lead.company_name}) just booked a call "
        f"for {when}.\n\nContact: {lead.contact_email}\nICP score: {lead.icp_score}\n"
        f"Why they qualified: {lead.score_reasons}"
    )
    notify_owner_now(settings, f"📅 Meeting booked: {lead.company_name}", body)


def send_owner_digest(session: Session, settings: Settings) -> dict[str, int]:
    state = session.query(DigestState).first()
    if state is None:
        state = DigestState()
        session.add(state)
        session.commit()

    since = state.last_sent_at or datetime.min.replace(tzinfo=timezone.utc)

    pending = (
        session.query(Message).filter(Message.status == MessageStatus.PENDING_APPROVAL.value).all()
    )
    new_hot_leads = (
        session.query(Lead)
        .filter(Lead.status == LeadStatus.QUEUED.value, Lead.created_at >= since)
        .order_by(Lead.icp_score.desc())
        .all()
    )
    new_meetings = session.query(Meeting).filter(Meeting.created_at >= since).all()
    replies_needing_attention = (
        session.query(Lead)
        .filter(Lead.status.in_([LeadStatus.REPLIED.value, LeadStatus.INTERESTED.value]))
        .filter(Lead.updated_at >= since)
        .all()
    )

    stats = {
        "pending_approvals": len(pending),
        "new_hot_leads": len(new_hot_leads),
        "new_meetings": len(new_meetings),
        "replies_needing_attention": len(replies_needing_attention),
    }

    if not any(stats.values()):
        logger.info("Nothing new since last digest; skipping owner email.")
        return stats

    lines = ["Your outbound lead-gen bot has updates:\n"]
    if pending:
        lines.append(f"📝 {len(pending)} draft(s) waiting on your approval in the review UI.")
    if new_hot_leads:
        lines.append(f"\n🔥 {len(new_hot_leads)} new high-fit lead(s) sourced and queued:")
        for lead in new_hot_leads[:10]:
            lines.append(f"  - {lead.contact_name} @ {lead.company_name} (score {lead.icp_score})")
    if new_meetings:
        lines.append(f"\n📅 {len(new_meetings)} meeting(s) booked.")
    if replies_needing_attention:
        lines.append(f"\n💬 {len(replies_needing_attention)} conversation(s) need your eyes:")
        for lead in replies_needing_attention[:10]:
            lines.append(f"  - {lead.contact_name} @ {lead.company_name} — status: {lead.status}")

    _send_owner_email(settings, "Outbound bot: daily update", "\n".join(lines))
    state.last_sent_at = datetime.now(timezone.utc)
    session.commit()
    return stats
