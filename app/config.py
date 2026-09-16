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

    # Gmail
    gmail_sender_email: str = ""
    gmail_credentials_path: str = "data/credentials.json"
    gmail_token_path: str = "data/token.json"

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

    # --- Owner phone (SMS notifications, and identifying your own manual
    # calls when they come in through the Twilio recorded line) ---
    owner_phone_number: str = ""

    # --- Dialer (Twilio + Vapi/Retell) ---
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""
    voice_agent_provider: str = "vapi"  # or "retell"
    vapi_api_key: str = ""
    vapi_assistant_id: str = ""
    vapi_webhook_secret: str = ""      # verifies the X-Vapi-Secret header on /dialer/webhooks/call-ended
    retell_api_key: str = ""
    retell_agent_id: str = ""
    retell_webhook_secret: str = ""    # verifies the X-Retell-Signature header on /dialer/webhooks/call-ended
    dialer_window_start_hour: int = 9   # local hour, inclusive
    dialer_window_end_hour: int = 17    # local hour, exclusive
    dialer_timezone: str = "America/New_York"
    dialer_max_concurrency: int = 3
    dialer_max_retries: int = 3
    dialer_retry_spacing_minutes: int = 120
    dialer_cooldown_hours: int = 24

    # --- Call intelligence (Zoom + the Twilio recorded line for manual calls) ---
    zoom_account_id: str = ""
    zoom_client_id: str = ""
    zoom_client_secret: str = ""
    zoom_webhook_secret_token: str = ""
    twilio_recorded_line_number: str = ""  # the Twilio number you route manual business calls through
    deadline_max_reminders: int = 5

    # --- Contracts (PandaDoc or DocuSign) ---
    contracts_provider: str = "pandadoc"  # or "docusign"
    pandadoc_api_key: str = ""
    docusign_integration_key: str = ""
    docusign_user_id: str = ""
    docusign_account_id: str = ""
    docusign_private_key_path: str = "data/docusign_private_key.pem"
    docusign_base_url: str = "https://demo.docusign.net/restapi"
    contract_template_id: str = ""
    pandadoc_webhook_shared_key: str = ""  # verifies PandaDoc's webhook signature
    docusign_connect_hmac_key: str = ""    # verifies DocuSign Connect's X-DocuSign-Signature-1 header

    # --- Notifications (Stripe webhook + Twilio SMS delivery) ---
    stripe_api_key: str = ""
    stripe_webhook_secret: str = ""

    # --- Sync / CRM auto-maintenance (dual-inbox Gmail monitoring + re-enrichment) ---
    # One registered OAuth app (client id/secret), one refresh token per
    # inbox — each inbox is authorized separately but shares the same app.
    # Distinct from GMAIL_* above, which is the file-token-based flow used
    # to send/poll the single outreach sending inbox.
    gmail_oauth_client_id: str = ""
    gmail_oauth_client_secret: str = ""
    gmail_business_refresh_token: str = ""
    gmail_personal_refresh_token: str = ""
    vibe_prospecting_api_key: str = ""
    sync_stale_prospect_days: int = 60

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
