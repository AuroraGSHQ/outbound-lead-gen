"""Central settings, loaded from environment variables / .env.

Everything the bot needs to know about *how* to run lives here. Nothing in
this module talks to a network or a database — it's pure configuration.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"

    # Lead sourcing
    apollo_api_key: str = ""

    # Scanner agent (optional — review-count/recency check is skipped without it)
    google_places_api_key: str = ""

    # Ads agent — real publishing stays off until these are set (see
    # app/integrations/google_ads.py / meta_ads.py)
    google_ads_developer_token: str = ""
    meta_access_token: str = ""

    # Aletheia (self-audit) — Aurora's own domain, checked with the same
    # passive checks Momus (scanner) runs on prospects. Blank = skipped.
    own_domain: str = ""

    # Gmail
    gmail_sender_email: str = ""
    gmail_credentials_path: str = "data/credentials.json"
    gmail_token_path: str = "data/token.json"

    # Twilio (Peitho's phone channel — SMS + outbound voice)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""

    # ElevenLabs (text-to-speech for Peitho's voice channel)
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    voice_clip_dir: str = "data/voice_clips"
    # Publicly reachable base URL for this app (e.g. https://olympus.up.railway.app) —
    # Twilio fetches the synthesized voice clip from here, so it must be a real
    # internet-reachable HTTPS URL, not localhost.
    public_base_url: str = ""

    # Calendly
    calendly_booking_link: str = ""
    calendly_webhook_signing_key: str = ""

    # Business identity / voice
    business_name: str = "Your Company"
    business_pitch: str = ""
    business_physical_address: str = ""
    sender_name: str = ""
    sender_title: str = ""

    # Owner + approvals
    owner_email: str = ""
    owner_ui_username: str = "owner"
    owner_ui_password: str = "change-me"

    # Behavior
    approval_mode: str = "draft_and_approve"  # or "autonomous"
    daily_outreach_cap: int = 25
    icp_config_path: str = "config/icp.yaml"
    database_url: str = "sqlite:///./data/leadgen.db"

    # Web app
    app_secret_key: str = "change-me"
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    @property
    def is_autonomous(self) -> bool:
        return self.approval_mode.strip().lower() == "autonomous"

    def require_for_sending(self) -> list[str]:
        """Return a list of human-readable problems that would block sending."""
        problems = []
        if not self.anthropic_api_key:
            problems.append("ANTHROPIC_API_KEY is not set")
        if not self.gmail_sender_email:
            problems.append("GMAIL_SENDER_EMAIL is not set")
        if not Path(self.gmail_token_path).exists():
            problems.append(
                f"Gmail token not found at {self.gmail_token_path} — run scripts/gmail_auth.py"
            )
        if not self.owner_email:
            problems.append("OWNER_EMAIL is not set")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
