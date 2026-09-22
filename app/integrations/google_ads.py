"""Real Google Ads API publishing — not wired up yet.

Nothing in services/ads.py calls this today; it generates the full campaign
brief (budget, targeting, copy) for a person to paste into Google Ads by
hand, which is the safer default until you have real API access (Google
requires a developer token application + review before the Ads API can
create anything).

Once you have that: set GOOGLE_ADS_DEVELOPER_TOKEN / GOOGLE_ADS_CLIENT_ID /
GOOGLE_ADS_CLIENT_SECRET / GOOGLE_ADS_REFRESH_TOKEN / GOOGLE_ADS_CUSTOMER_ID
in .env, add the `google-ads` package to requirements.txt, and implement
create_paused_campaign() below using their SDK. ALWAYS create campaigns
paused — the same draft-and-approve principle as every other send in this
app; a human flips them live.
"""
from __future__ import annotations

from app.config import Settings


class NotConfiguredError(RuntimeError):
    pass


def is_configured(settings: Settings) -> bool:
    return bool(getattr(settings, "google_ads_developer_token", ""))


def create_paused_campaign(settings: Settings, campaign_brief: dict) -> str:
    if not is_configured(settings):
        raise NotConfiguredError(
            "Google Ads API isn't configured. Complete Google's developer token "
            "application, set GOOGLE_ADS_* in .env, add the google-ads package, "
            "and implement this using their SDK. Until then, use the generated "
            "brief on the Ads page to create the campaign by hand (paused) in "
            "Google Ads."
        )
    raise NotImplementedError("Wire up the google-ads SDK call here once configured.")
