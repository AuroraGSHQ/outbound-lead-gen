#!/usr/bin/env python3
"""Manually trigger one lead-sourcing sweep (useful for testing your ICP config
and Apollo key before trusting the scheduler with it)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.services.sourcing import run_sourcing_sweep  # noqa: E402

if __name__ == "__main__":
    init_db()
    settings = get_settings()
    session = SessionLocal()
    try:
        print(run_sourcing_sweep(session, settings))
    finally:
        session.close()
