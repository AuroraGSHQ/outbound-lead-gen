#!/usr/bin/env python3
"""Manually trigger one owner-digest email (useful to confirm Gmail sending works)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.services.notify import send_owner_digest  # noqa: E402

if __name__ == "__main__":
    init_db()
    settings = get_settings()
    session = SessionLocal()
    try:
        print(send_owner_digest(session, settings))
    finally:
        session.close()
