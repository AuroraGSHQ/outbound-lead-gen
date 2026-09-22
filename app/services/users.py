"""User accounts: hashing, creation, authentication, and the one-time owner
bootstrap that keeps the existing OWNER_UI_USERNAME/PASSWORD quick-start
working on a fresh database.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import bcrypt
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import User, UserRole

logger = logging.getLogger(__name__)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_user(
    session: Session, *, name: str, email: str, password: str, role: str = UserRole.OPS.value
) -> User:
    email = email.strip().lower()
    existing = session.query(User).filter(User.email == email).first()
    if existing:
        raise ValueError(f"A user with email {email} already exists")
    user = User(name=name.strip(), email=email, password_hash=hash_password(password), role=role)
    session.add(user)
    session.commit()
    return user


def authenticate(session: Session, email: str, password: str) -> User | None:
    email = email.strip().lower()
    user = session.query(User).filter(User.email == email, User.active.is_(True)).first()
    if user is None or not verify_password(password, user.password_hash):
        return None
    user.last_login_at = datetime.now(timezone.utc)
    session.commit()
    return user


def list_users(session: Session) -> list[User]:
    return session.query(User).order_by(User.created_at.asc()).all()


def ensure_seed_owner(session: Session, settings: Settings) -> None:
    """If there are no users yet, seed one owner account from .env so the
    quick-start flow (OWNER_UI_USERNAME/PASSWORD) still works on first run.
    Every additional teammate is created from the /users admin page after
    that, or via scripts/create_user.py."""
    if session.query(User).count() > 0:
        return
    if not settings.owner_ui_username or not settings.owner_ui_password:
        logger.warning(
            "No users exist and OWNER_UI_USERNAME/PASSWORD are unset — "
            "create the first account with scripts/create_user.py."
        )
        return
    email = settings.owner_ui_username.strip().lower()
    if "@" not in email:
        email = f"{email}@local"
    create_user(session, name=settings.owner_ui_username, email=email, password=settings.owner_ui_password, role=UserRole.OWNER.value)
    logger.info("Seeded owner account %s from OWNER_UI_USERNAME/PASSWORD.", email)
