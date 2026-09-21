"""CSV import for leads sourced outside Apollo — currently used for Vibe
Prospecting exports (see services/sourcing_requests.py), written generically
enough to take any CSV with a header row. No assumptions about a fixed
export schema: `suggest_mapping` offers a best-effort guess per column, but
the person importing confirms (or overrides) the mapping before anything is
written — the same "don't trust it blind" posture as the rest of the app.

Imported leads are scored against the same ICP (config/icp.yaml) as Apollo
leads, via the same app.icp.score_lead, so they enter the pipeline
identically from that point on.
"""
from __future__ import annotations

import csv
import io
import logging

from sqlalchemy.orm import Session

from app.config import Settings
from app.icp import ICPConfig, score_lead
from app.models import Lead, LeadStatus

logger = logging.getLogger(__name__)

# The Lead fields a CSV column can be mapped to. contact_email is the only
# one required for a row to import at all.
LEAD_FIELDS = [
    "contact_email",
    "contact_name",
    "contact_title",
    "company_name",
    "domain",
    "industry",
    "company_size",
    "location",
    "linkedin_url",
]

_FIELD_GUESSES: dict[str, list[str]] = {
    "contact_email": ["prospect_email", "email"],
    "contact_name": ["prospect_full_name", "full_name", "prospect_name", "contact_name", "name"],
    "contact_title": ["prospect_job_title", "job_title", "title"],
    "company_name": ["company_name", "business_name", "company"],
    "domain": ["company_website", "website", "domain"],
    "industry": ["naics_category", "linkedin_category", "industry", "category"],
    "company_size": ["company_employee_count", "employee_count", "company_size", "size"],
    "location": [
        "company_region_country_code",
        "company_country_code",
        "prospect_region_country_code",
        "prospect_country_code",
        "location",
        "city",
        "country",
    ],
    "linkedin_url": ["prospect_linkedin_url", "linkedin_url", "linkedin"],
}


def parse_headers(csv_text: str) -> list[str]:
    reader = csv.reader(io.StringIO(csv_text))
    try:
        return [h.strip() for h in next(reader)]
    except StopIteration:
        return []


def suggest_mapping(headers: list[str]) -> dict[str, str]:
    """Best-effort guess only — always shown to a person to confirm/override
    before import, never applied silently.

    Two passes on purpose: exact header matches are claimed before any
    substring matching happens, so a short, generic candidate like "name"
    can't greedily claim "company_name" ahead of company_name's own exact
    match being tried.
    """
    used: set[str] = set()
    mapping: dict[str, str] = {}

    def _find(candidates: list[str], *, exact: bool) -> str | None:
        for candidate in candidates:
            for h in headers:
                if h in used:
                    continue
                lh = h.lower()
                if (lh == candidate) if exact else (candidate in lh):
                    return h
        return None

    for field, candidates in _FIELD_GUESSES.items():
        match = _find(candidates, exact=True)
        if match:
            mapping[field] = match
            used.add(match)

    for field, candidates in _FIELD_GUESSES.items():
        if field in mapping:
            continue
        match = _find(candidates, exact=False)
        if match:
            mapping[field] = match
            used.add(match)

    return mapping


def _parse_company_size(raw: str) -> int | None:
    if not raw:
        return None
    digits = "".join(ch for ch in raw if ch.isdigit() or ch == "-")
    if not digits:
        return None
    first = digits.split("-")[0]
    return int(first) if first.isdigit() else None


def import_leads_from_csv(
    session: Session,
    settings: Settings,
    csv_text: str,
    column_mapping: dict[str, str],
    *,
    source: str = "vibe_prospecting",
    default_notes: str = "",
) -> dict[str, int]:
    icp = ICPConfig.load(settings.icp_config_path)
    stats = {
        "rows": 0,
        "imported": 0,
        "duplicates": 0,
        "excluded": 0,
        "queued": 0,
        "missing_email": 0,
    }

    email_column = column_mapping.get("contact_email", "")
    reader = csv.DictReader(io.StringIO(csv_text))
    for row in reader:
        stats["rows"] += 1
        email = (row.get(email_column, "") or "").strip().lower()
        if not email or "@" not in email:
            stats["missing_email"] += 1
            continue

        existing = session.query(Lead).filter(Lead.contact_email == email).first()
        if existing:
            stats["duplicates"] += 1
            continue

        candidate = {
            "contact_email": email,
            "contact_name": row.get(column_mapping.get("contact_name", ""), "") or "",
            "contact_title": row.get(column_mapping.get("contact_title", ""), "") or "",
            "company_name": row.get(column_mapping.get("company_name", ""), "") or "",
            "domain": (row.get(column_mapping.get("domain", ""), "") or "").lower(),
            "industry": row.get(column_mapping.get("industry", ""), "") or "",
            "location": row.get(column_mapping.get("location", ""), "") or "",
            "linkedin_url": row.get(column_mapping.get("linkedin_url", ""), "") or "",
            "company_size": _parse_company_size(row.get(column_mapping.get("company_size", ""), "") or ""),
            "description": default_notes,
        }

        scored = score_lead(candidate, icp)
        if scored.excluded:
            stats["excluded"] += 1
            continue

        status = LeadStatus.QUEUED.value if scored.score >= icp.min_score_to_contact else LeadStatus.NEW.value
        lead = Lead(
            company_name=candidate["company_name"],
            domain=candidate["domain"],
            industry=candidate["industry"],
            company_size=candidate["company_size"],
            location=candidate["location"],
            contact_name=candidate["contact_name"],
            contact_title=candidate["contact_title"],
            contact_email=email,
            linkedin_url=candidate["linkedin_url"],
            source=source,
            icp_score=scored.score,
            score_reasons="; ".join(scored.reasons),
            status=status,
            notes=default_notes,
        )
        session.add(lead)
        stats["imported"] += 1
        if status == LeadStatus.QUEUED.value:
            stats["queued"] += 1

    session.commit()
    logger.info("CSV lead import complete: %s", stats)
    return stats
