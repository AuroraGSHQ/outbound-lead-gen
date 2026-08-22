"""Ideal Customer Profile loading + lead scoring.

Scoring is deliberately simple and explainable (a weighted checklist, not a
black box) so you can look at score_reasons on a Lead and see exactly why it
did or didn't qualify as one of the "best clients."
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ICPConfig:
    target_industries: list[str] = field(default_factory=list)
    target_titles: list[str] = field(default_factory=list)
    company_size_min: int | None = None
    company_size_max: int | None = None
    target_locations: list[str] = field(default_factory=list)
    keywords_boost: list[str] = field(default_factory=list)
    exclude_domains: list[str] = field(default_factory=list)
    min_score_to_contact: float = 0.6

    @classmethod
    def load(cls, path: str) -> "ICPConfig":
        p = Path(path)
        if not p.exists():
            example = Path("config/icp.example.yaml")
            raise FileNotFoundError(
                f"ICP config not found at {path}. Copy {example} to {path} and edit it."
            )
        raw: dict[str, Any] = yaml.safe_load(p.read_text()) or {}
        return cls(
            target_industries=raw.get("target_industries", []),
            target_titles=raw.get("target_titles", []),
            company_size_min=raw.get("company_size_min"),
            company_size_max=raw.get("company_size_max"),
            target_locations=raw.get("target_locations", []),
            keywords_boost=raw.get("keywords_boost", []),
            exclude_domains=[d.lower() for d in raw.get("exclude_domains", [])],
            min_score_to_contact=float(raw.get("min_score_to_contact", 0.6)),
        )


@dataclass
class ScoredLead:
    score: float
    reasons: list[str]
    excluded: bool = False


def _title_matches(contact_title: str, target_titles: list[str]) -> bool:
    t = contact_title.lower()
    return any(target.lower() in t for target in target_titles)


def score_lead(candidate: dict[str, Any], icp: ICPConfig) -> ScoredLead:
    """Score a candidate lead dict against the ICP.

    Expected keys (all optional, missing ones just score 0 for that factor):
    company_name, domain, industry, company_size, location, contact_title,
    contact_email, description (free text used for keyword matching).
    """
    domain = (candidate.get("domain") or "").lower()
    for excluded_domain in icp.exclude_domains:
        if domain == excluded_domain or domain.endswith(f".{excluded_domain}"):
            return ScoredLead(score=0.0, reasons=[f"excluded domain: {domain}"], excluded=True)

    reasons: list[str] = []
    score = 0.0

    # Industry match — 0.3
    industry = (candidate.get("industry") or "").lower()
    if icp.target_industries and any(
        ti.lower() in industry or industry in ti.lower() for ti in icp.target_industries
    ):
        score += 0.3
        reasons.append(f"industry '{candidate.get('industry')}' matches ICP")
    elif not icp.target_industries:
        score += 0.3  # no industry filter configured -> don't penalize

    # Title match — 0.25
    contact_title = candidate.get("contact_title") or ""
    if icp.target_titles and _title_matches(contact_title, icp.target_titles):
        score += 0.25
        reasons.append(f"title '{contact_title}' matches target titles")
    elif not icp.target_titles:
        score += 0.25

    # Company size within range — 0.2
    size = candidate.get("company_size")
    if size is not None:
        lo = icp.company_size_min if icp.company_size_min is not None else 0
        hi = icp.company_size_max if icp.company_size_max is not None else float("inf")
        if lo <= size <= hi:
            score += 0.2
            reasons.append(f"company size {size} within [{lo}, {hi}]")
    else:
        score += 0.1  # unknown size, partial credit

    # Location match — 0.15
    location = (candidate.get("location") or "").lower()
    if icp.target_locations and any(loc.lower() in location for loc in icp.target_locations):
        score += 0.15
        reasons.append(f"location '{candidate.get('location')}' matches ICP")
    elif not icp.target_locations:
        score += 0.15

    # Keyword boosts — up to 0.1
    text = " ".join(
        str(candidate.get(k, "")) for k in ("description", "company_name", "industry")
    ).lower()
    hits = [kw for kw in icp.keywords_boost if kw.lower() in text]
    if hits:
        boost = min(0.1, 0.03 * len(hits))
        score += boost
        reasons.append(f"keyword boost from: {', '.join(hits)}")

    score = round(min(score, 1.0), 3)
    if not reasons:
        reasons.append("no strong ICP signals found")
    return ScoredLead(score=score, reasons=reasons)
