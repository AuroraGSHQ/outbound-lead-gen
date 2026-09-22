"""Find candidate leads via Apollo.io, score them against the ICP, store new ones.

This module never sends anything — it only populates the `leads` table.
Deciding who actually gets emailed happens in services/outreach.py.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.config import Settings
from app.icp import ICPConfig, score_lead
from app.integrations.apollo import ApolloClient
from app.models import Lead, LeadStatus

logger = logging.getLogger(__name__)


def run_sourcing_sweep(session: Session, settings: Settings, *, pages: int = 2) -> dict[str, int]:
    icp = ICPConfig.load(settings.icp_config_path)
    client = ApolloClient(settings.apollo_api_key)
    stats = {"fetched": 0, "new": 0, "duplicates": 0, "excluded": 0, "queued": 0}

    try:
        for page in range(1, pages + 1):
            candidates = client.search_people(
                industries=icp.target_industries,
                titles=icp.target_titles,
                locations=icp.target_locations,
                company_size_min=icp.company_size_min,
                company_size_max=icp.company_size_max,
                page=page,
            )
            if not candidates:
                break
            stats["fetched"] += len(candidates)

            for candidate in candidates:
                email = candidate.get("contact_email", "").lower().strip()
                if not email:
                    continue
                existing = session.query(Lead).filter(Lead.contact_email == email).first()
                if existing:
                    stats["duplicates"] += 1
                    continue

                scored = score_lead(candidate, icp)
                if scored.excluded:
                    stats["excluded"] += 1
                    continue

                status = (
                    LeadStatus.QUEUED.value
                    if scored.score >= icp.min_score_to_contact
                    else LeadStatus.NEW.value
                )
                lead = Lead(
                    company_name=candidate.get("company_name", ""),
                    domain=candidate.get("domain", ""),
                    industry=candidate.get("industry", ""),
                    company_size=candidate.get("company_size"),
                    location=candidate.get("location", ""),
                    contact_name=candidate.get("contact_name", ""),
                    contact_title=candidate.get("contact_title", ""),
                    contact_email=email,
                    linkedin_url=candidate.get("linkedin_url", ""),
                    source=candidate.get("source", "apollo"),
                    icp_score=scored.score,
                    score_reasons="; ".join(scored.reasons),
                    status=status,
                    notes=candidate.get("description", ""),
                )
                session.add(lead)
                stats["new"] += 1
                if status == LeadStatus.QUEUED.value:
                    stats["queued"] += 1
            session.commit()
    finally:
        client.close()

    logger.info("Sourcing sweep complete: %s", stats)
    return stats
