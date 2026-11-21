"""JWT authentication, refresh token rotation, and user profile serialization (FR-001, FR-002)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.security import check_password_hash

from app.common.errors import ForbiddenError, UnauthorizedError, ValidationError
from app.common.rbac import get_role_permissions, resolve_user_region_ids
from app.config import settings
from app.infrastructure.database.extensions import utcnow
from app.modules.auth.models import RefreshToken
from app.modules.users.models import User


def serialize_user_profile(user: User) -> dict:
    """Serialize authenticated user with role, permissions, and region scope (FR-002)."""
    role_code = user.role.code if user.role else ""
    regions = [
        {
            "id": ur.region.id,
            "code": ur.region.code,
            "name": ur.region.name,
        }
        for ur in sorted(user.user_regions or [], key=lambda item: item.region.name)
        if ur.region is not None
    ]
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "full_name": user.full_name,
        "is_active": user.is_active,
        "is_on_duty": user.is_on_duty,
        "role": {
            "id": user.role.id,
            "code": role_code,
            "name": user.role.name,
        }
        if user.role
        else None,
        "permissions": get_role_permissions(role_code),
        "scope_type": "global" if resolve_user_region_ids(user) is None else "regional",
        "regions": regions,
    }


def _issue_token_pair(session: Session, user: User) -> dict:
    now = datetime.now(timezone.utc)
    access_exp = now + timedelta(minutes=settings.jwt_access_expires_minutes)
    refresh_exp = now + timedelta(days=settings.jwt_refresh_expires_days)
    refresh_jti = uuid.uuid4().hex

    access_payload = {
        "sub": str(user.id),
        "role": user.role.code if user.role else "",
        "type": "access",
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int(access_exp.timestamp()),
    }
    refresh_payload = {
        "sub": str(user.id),
        "type": "refresh",
        "jti": refresh_jti,
        "iat": int(now.timestamp()),
        "exp": int(refresh_exp.timestamp()),
    }

    access_token = jwt.encode(access_payload, settings.jwt_secret_key, algorithm="HS256")
    refresh_token = jwt.encode(refresh_payload, settings.jwt_secret_key, algorithm="HS256")

    session.add(
        RefreshToken(
            jti=refresh_jti,
            user_id=user.id,
            expires_at=refresh_exp,
        )
    )
    session.commit()

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
        "expires_in": settings.jwt_access_expires_minutes * 60,
        "user": serialize_user_profile(user),
    }


def authenticate_user(session: Session, username: str, password: str) -> dict:
    clean_user = (username or "").strip()
    if not clean_user or not password:
        raise ValidationError("Username dan password wajib diisi.")

    user = session.scalar(select(User).where(User.username == clean_user))
    if user is None or not check_password_hash(user.password_hash, password):
        raise UnauthorizedError("Username atau password salah.")

    if not user.is_active:
        raise ForbiddenError("Akun Anda sedang dinonaktifkan. Hubungi Admin.")

    return _issue_token_pair(session, user)


def refresh_access_token(session: Session, refresh_token_str: str) -> dict:
    if not refresh_token_str or not refresh_token_str.strip():
        raise ValidationError("refresh_token wajib diisi.")

    try:
        payload = jwt.decode(
            refresh_token_str.strip(),
            settings.jwt_secret_key,
            algorithms=["HS256"],
        )
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("Refresh token telah kedaluwarsa. Silakan login ulang.") from exc
    except jwt.InvalidTokenError as exc:
        raise UnauthorizedError("Refresh token tidak valid.") from exc

    if payload.get("type") != "refresh":
        raise UnauthorizedError("Token bukan refresh token.")

    jti = str(payload.get("jti") or "")
    token_row = session.scalar(select(RefreshToken).where(RefreshToken.jti == jti))
    if token_row is None or token_row.revoked_at is not None:
        raise UnauthorizedError("Refresh token sudah dicabut atau tidak ditemukan.")

    user = session.scalar(select(User).where(User.id == token_row.user_id))
    if user is None:
        raise UnauthorizedError("Pengguna tidak ditemukan.")
    if not user.is_active:
        raise ForbiddenError("Akun Anda sedang dinonaktifkan.")

    # Rotate refresh token: revoke old token immediately
    token_row.revoked_at = utcnow()
    session.flush()
    return _issue_token_pair(session, user)


def revoke_refresh_token(session: Session, refresh_token_str: str, *, user_id: int) -> bool:
    if not refresh_token_str or not refresh_token_str.strip():
        return False

    try:
        payload = jwt.decode(
            refresh_token_str.strip(),
            settings.jwt_secret_key,
            algorithms=["HS256"],
            options={"verify_exp": False},
        )
    except jwt.InvalidTokenError:
        return False

    if payload.get("type") != "refresh":
        return False

    jti = str(payload.get("jti") or "")
    token_row = session.scalar(
        select(RefreshToken).where(RefreshToken.jti == jti, RefreshToken.user_id == user_id)
    )
    if token_row is None or token_row.revoked_at is not None:
        return False

    token_row.revoked_at = utcnow()
    session.commit()
    return True
