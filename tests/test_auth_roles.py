import pytest
from fastapi import HTTPException

from app.models import User, UserRole
from app.services.users import hash_password, verify_password
from app.web.auth import require_roles


def test_password_hash_roundtrip():
    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed) is True
    assert verify_password("wrong password", hashed) is False


def _user(role: str) -> User:
    return User(id=1, name="Test User", email="t@example.com", password_hash="x", role=role)


def test_owner_passes_any_role_requirement():
    dependency = require_roles("marketing")
    result = dependency(user=_user(UserRole.OWNER.value))
    assert result.role == UserRole.OWNER.value


def test_matching_role_passes():
    dependency = require_roles("marketing")
    user = _user(UserRole.MARKETING.value)
    assert dependency(user=user) is user


def test_mismatched_role_is_forbidden():
    dependency = require_roles("marketing")
    user = _user(UserRole.SALES.value)
    with pytest.raises(HTTPException) as exc_info:
        dependency(user=user)
    assert exc_info.value.status_code == 403


def test_no_roles_listed_means_owner_only():
    dependency = require_roles()
    assert dependency(user=_user(UserRole.OWNER.value)) is not None
    with pytest.raises(HTTPException):
        dependency(user=_user(UserRole.OPS.value))
