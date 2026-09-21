"""The request/fulfill loop for scouting data sources that can't be called
directly from the backend. Vibe Prospecting (Explorium) is credit-metered
and its own tool rules require a human to see the cost and explicitly
confirm before every export — that's a deliberate guardrail against
runaway spend, not a gap to route around. So instead of a silent scheduled
job, a teammate fills in what they're looking for here, gets back a
ready-to-paste prompt for a Vibe-Prospecting-enabled Claude session, runs it
themselves (reviewing the cost as they go), and imports the resulting CSV
export with services/lead_import.py.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import SourcingRequest, SourcingRequestStatus


def create_request(session: Session, *, requested_by_user_id: int | None, criteria: dict) -> SourcingRequest:
    request = SourcingRequest(requested_by_user_id=requested_by_user_id, criteria=criteria)
    session.add(request)
    session.commit()
    return request


def build_prompt(request: SourcingRequest) -> str:
    c = request.criteria or {}
    lines = ["Find prospects for outbound using Vibe Prospecting:"]
    if c.get("industry"):
        lines.append(f"- Industry / business type: {c['industry']}")
    if c.get("job_titles"):
        lines.append(f"- Job titles: {c['job_titles']}")
    if c.get("locations"):
        lines.append(f"- Location: {c['locations']}")
    if c.get("company_size"):
        lines.append(f"- Company size: {c['company_size']} employees")
    if c.get("company_revenue"):
        lines.append(f"- Company revenue: {c['company_revenue']}")
    if c.get("keywords"):
        lines.append(f"- Keywords / signals: {c['keywords']}")
    lines.append(f"- Number of results: {c.get('number_of_results') or 30}")
    lines.append("- Only prospects with an available email address.")
    if c.get("notes"):
        lines.append(f"- Notes: {c['notes']}")
    lines.append(
        "\nShow me the cost estimate before exporting, and only export to CSV once I've "
        "confirmed. Once it's exported, give me the download link."
    )
    return "\n".join(lines)


def list_requests(session: Session) -> list[SourcingRequest]:
    return session.query(SourcingRequest).order_by(SourcingRequest.created_at.desc()).all()


def get_request(session: Session, request_id: int) -> SourcingRequest | None:
    return session.get(SourcingRequest, request_id)


def store_pending_csv(session: Session, request_id: int, csv_text: str) -> SourcingRequest:
    request = session.get(SourcingRequest, request_id)
    if request is None:
        raise ValueError(f"No sourcing request {request_id}")
    request.pending_csv = csv_text
    session.commit()
    return request


def mark_fulfilled(session: Session, request_id: int, imported_count: int) -> SourcingRequest:
    request = session.get(SourcingRequest, request_id)
    if request is None:
        raise ValueError(f"No sourcing request {request_id}")
    request.status = SourcingRequestStatus.FULFILLED.value
    request.imported_lead_count += imported_count
    request.fulfilled_at = datetime.now(timezone.utc)
    request.pending_csv = ""
    session.commit()
    return request
