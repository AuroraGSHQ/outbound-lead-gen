"""Session-cookie auth + role-based access, replacing the old single-owner
HTTP Basic login now that there's a real team of users.

The cookie holds nothing but a signed, timestamped user id (itsdangerous —
already a dependency, no extra session store needed). `NotAuthenticated` is
caught by an exception handler registered in main.py that redirects to
/login, so routes can just `Depends(require_login)` and not think about it.
"""
from __future__ import annotations

from collections.abc import Iterable

from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.models import User

SESSION_COOKIE_NAME = "session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 14  # 14 days


class NotAuthenticated(Exception):
    """Raised when a page requires login and there's no valid session
    cookie. Caught by main.py's exception handler, which redirects to
    /login rather than showing a bare error."""


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.app_secret_key, salt="aurora-session")


def make_session_cookie_value(settings: Settings, user_id: int) -> str:
    return _serializer(settings).dumps({"uid": user_id})


def _user_id_from_cookie(settings: Settings, cookie_value: str) -> int | None:
    try:
        data = _serializer(settings).loads(cookie_value, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    return data.get("uid")


def _get_db() -> Session:
    return SessionLocal()


def require_login(request: Request, settings: Settings = Depends(get_settings)) -> User:
    cookie_value = request.cookies.get(SESSION_COOKIE_NAME)
    if not cookie_value:
        raise NotAuthenticated()
    user_id = _user_id_from_cookie(settings, cookie_value)
    if user_id is None:
        raise NotAuthenticated()

    db = _get_db()
    try:
        user = db.get(User, user_id)
        if user is None or not user.active:
            raise NotAuthenticated()
        db.expunge(user)
        return user
    finally:
        db.close()


def require_roles(*roles: str):
    """FastAPI dependency factory: Depends(require_roles("owner", "sales")).
    Owner always passes, regardless of the roles listed."""

    def _dependency(user: User = Depends(require_login)) -> User:
        allowed: Iterable[str] = {*roles, "owner"}
        if user.role not in allowed:
            raise HTTPException(status_code=403, detail="You don't have access to this page.")
        return user

    return _dependency
