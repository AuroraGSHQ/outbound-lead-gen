#!/usr/bin/env python3
"""CLI fallback for creating (or resetting) a team account, for when you'd
rather not use the Users admin page in the dashboard — or need to create the
very first owner account without OWNER_UI_USERNAME/PASSWORD set.

Usage:
    python scripts/create_user.py --name "Alex Rivera" --email alex@yourcompany.com --role sales
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import User, UserRole  # noqa: E402
from app.services.users import create_user, hash_password  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--role", choices=[r.value for r in UserRole], default=UserRole.OPS.value)
    parser.add_argument("--reset-password", action="store_true", help="Reset an existing user's password instead of creating a new one.")
    args = parser.parse_args()

    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        raise SystemExit("Passwords didn't match.")

    init_db()
    session = SessionLocal()
    try:
        if args.reset_password:
            user = session.query(User).filter(User.email == args.email.lower().strip()).first()
            if user is None:
                raise SystemExit(f"No user with email {args.email}")
            user.password_hash = hash_password(password)
            session.commit()
            print(f"Password reset for {user.email}.")
        else:
            user = create_user(session, name=args.name, email=args.email, password=password, role=args.role)
            print(f"Created {user.role} account for {user.email}.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
