"""Passive, read-only checks against a prospect's site for the Broken-Funnel
Scanner (manual §8). Deliberately does NOT submit forms or place calls — the
manual is explicit that those two checks are done by a human, once, for real,
never automated at volume. See services/scanner.py::_create_verification_task.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

import httpx

_GA_RE = re.compile(r"gtag\(|G-[A-Z0-9]{6,}|googletagmanager\.com/gtag")
_META_PIXEL_RE = re.compile(r"fbq\(|connect\.facebook\.net/.*?/fbevents")
_GOOGLE_ADS_RE = re.compile(r"AW-\d{6,}")
_VIEWPORT_RE = re.compile(r'<meta[^>]+name=["\']viewport["\']', re.IGNORECASE)
_TEL_RE = re.compile(r'href=["\']tel:', re.IGNORECASE)
_FORM_RE = re.compile(r"<form\b", re.IGNORECASE)

_USER_AGENT = "Mozilla/5.0 (compatible; AuroraGrowthOS-Scanner/1.0; passive read-only check)"


@dataclass
class SiteCheckResult:
    domain: str
    reachable: bool
    load_time_ms: int | None = None
    mobile_ok: bool | None = None
    tracking_present: bool | None = None
    click_to_call_present: bool | None = None
    has_contact_form: bool | None = None
    error: str = ""


def check_site(domain: str, *, timeout: float = 10.0) -> SiteCheckResult:
    url = domain if domain.startswith("http") else f"https://{domain}"
    start = time.monotonic()
    try:
        resp = httpx.get(
            url, timeout=timeout, follow_redirects=True, headers={"User-Agent": _USER_AGENT}
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)
    except httpx.HTTPError as exc:
        return SiteCheckResult(domain=domain, reachable=False, error=str(exc))

    html = resp.text
    return SiteCheckResult(
        domain=domain,
        reachable=resp.status_code < 400,
        load_time_ms=elapsed_ms,
        mobile_ok=bool(_VIEWPORT_RE.search(html)),
        tracking_present=bool(
            _GA_RE.search(html) or _META_PIXEL_RE.search(html) or _GOOGLE_ADS_RE.search(html)
        ),
        click_to_call_present=bool(_TEL_RE.search(html)),
        has_contact_form=bool(_FORM_RE.search(html)),
    )
