"""Eris (competitor watch) & Astraea (pricing benchmark) — both on-demand,
both writing to the same CompetitorNote table, split only by `note_type`.
There's no ad-transparency or scraping API here: a person logs what they
saw (an ad, a pricing page), and Claude runs it through the manual's own
"cover your logo" differentiation test.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.claude import BusinessProfile, ClaudeDrafter
from app.models import CompetitorNote, CompetitorNoteType


def _business_profile(settings: Settings) -> BusinessProfile:
    return BusinessProfile(
        business_name=settings.business_name,
        business_pitch=settings.business_pitch,
        sender_name=settings.sender_name,
        sender_title=settings.sender_title,
        calendly_link=settings.calendly_booking_link,
        physical_address=settings.business_physical_address,
    )


def log_note(
    session: Session,
    settings: Settings,
    *,
    competitor_name: str,
    note_type: str = CompetitorNoteType.AD.value,
    source_url: str = "",
    observed_text: str,
    logged_by_user_id: int | None = None,
) -> CompetitorNote:
    profile = _business_profile(settings)
    drafter = ClaudeDrafter(settings.anthropic_api_key, settings.anthropic_model)
    analysis = drafter.draft_competitor_analysis(competitor_name, observed_text, note_type, profile)

    note = CompetitorNote(
        competitor_name=competitor_name,
        note_type=note_type,
        source_url=source_url,
        observed_text=observed_text,
        analysis=analysis,
        logged_by_user_id=logged_by_user_id,
    )
    session.add(note)
    session.commit()
    return note


def list_notes(session: Session, *, note_type: str | None = None) -> list[CompetitorNote]:
    query = session.query(CompetitorNote)
    if note_type:
        query = query.filter(CompetitorNote.note_type == note_type)
    return query.order_by(CompetitorNote.created_at.desc()).all()
