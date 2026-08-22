#!/usr/bin/env python3
"""One-time interactive OAuth flow to authorize Gmail sending + reading.

Prerequisite: create an OAuth 2.0 Client ID (Desktop app) in Google Cloud
Console, enable the Gmail API, and download the client secret JSON to
data/credentials.json. See docs/SETUP.md for the click-by-click steps.

Run this locally (it opens a browser). The resulting token is written to
data/token.json and is what the running bot uses from then on — this script
never needs to run again unless you revoke access.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.integrations.gmail import SCOPES  # noqa: E402


def main() -> None:
    settings = get_settings()
    creds_path = Path(settings.gmail_credentials_path)
    if not creds_path.exists():
        raise SystemExit(
            f"Missing {creds_path}. Download your OAuth client secret from Google Cloud "
            "Console and save it there first (see docs/SETUP.md)."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
    creds = flow.run_local_server(port=0)

    token_path = Path(settings.gmail_token_path)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json())
    print(f"Wrote Gmail token to {token_path}. You're set — start the app normally now.")


if __name__ == "__main__":
    main()
