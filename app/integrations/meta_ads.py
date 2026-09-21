"""Real Meta Marketing API publishing — not wired up yet.

Same story as integrations/google_ads.py: services/ads.py generates the
campaign brief for manual publishing by default. Once you have a Meta app
with Marketing API access approved, set META_APP_ID / META_APP_SECRET /
META_ACCESS_TOKEN / META_AD_ACCOUNT_ID in .env, add the `facebook-business`
package to requirements.txt, and implement create_paused_campaign() below.
ALWAYS create campaigns paused — a human flips them live.
"""
from __future__ import annotations

from app.config import Settings


class NotConfiguredError(RuntimeError):
    pass


def is_configured(settings: Settings) -> bool:
    return bool(getattr(settings, "meta_access_token", ""))


def create_paused_campaign(settings: Settings, campaign_brief: dict) -> str:
    if not is_configured(settings):
        raise NotConfiguredError(
            "Meta Marketing API isn't configured. Get Marketing API access "
            "approved for your app, set META_* in .env, add the "
            "facebook-business package, and implement this using their SDK. "
            "Until then, use the generated brief on the Ads page to create the "
            "campaign by hand (paused) in Meta Ads Manager."
        )
    raise NotImplementedError("Wire up the facebook-business SDK call here once configured.")
