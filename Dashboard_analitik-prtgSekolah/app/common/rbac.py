"""Server-side RBAC, permission matrix, and region scope guards (ADR-011, ADR-012)."""

from __future__ import annotations

from functools import wraps
from typing import Optional

from flask import g, request
import jwt
from sqlalchemy import select

from app.common.errors import ForbiddenError, UnauthorizedError
from app.config import settings
from app.infrastructure.database import db
from app.modules.auth.models import (
    ROLE_ADMIN,
    ROLE_KOORDINATOR_TEKNISI,
    ROLE_SUPERVISOR_UTAMA,
    ROLE_SUPERVISOR_WILAYAH,
    ROLE_TEKNISI,
)
from app.modules.users.models import User

# Strict MVP 1.0 permission matrix (ADR-011):
# Admin NEVER receives `incidents.verify`.
ROLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    ROLE_ADMIN: (
        "users.manage",
        "roles.assign",
        "regions.manage",
        "system_config.manage",
        "monitoring.view_all",
        "monitoring.view_scope",
        "incidents.view_scope",
        "duty.manage",
        "audit.view",
    ),
    ROLE_SUPERVISOR_UTAMA: (
        "monitoring.view_all",
        "monitoring.view_scope",
        "incidents.view_scope",
        "assignments.manage",
        "duty.manage",
        "incidents.verify",
        "audit.view_operational",
    ),
    ROLE_SUPERVISOR_WILAYAH: (
        "monitoring.view_scope",
        "incidents.view_scope",
        "assignments.manage",
        "duty.manage",
        "incidents.verify",
        "audit.view_operational",
    ),
    ROLE_KOORDINATOR_TEKNISI: (
        "monitoring.view_scope",
        "incidents.view_scope",
        "assignments.manage",
        "duty.manage",
        "audit.view_operational",
    ),
    ROLE_TEKNISI: (
        "monitoring.view_scope",
        "incidents.view_scope",
        "responses.create",
        "resolution.submit",
    ),
}

GLOBAL_SCOPE_ROLES = {ROLE_ADMIN, ROLE_SUPERVISOR_UTAMA}
SINGLE_REGION_ROLES = {ROLE_SUPERVISOR_WILAYAH, ROLE_KOORDINATOR_TEKNISI}
MULTI_REGION_ROLES = {ROLE_TEKNISI}


def get_role_permissions(role_code: str) -> list[str]:
    return list(ROLE_PERMISSIONS.get(role_code, ()))


def user_has_permission(user: User, permission: str) -> bool:
    if user is None or not user.is_active or user.role is None:
        return False
    return permission in ROLE_PERMISSIONS.get(user.role.code, ())


def resolve_user_region_ids(user: User) -> Optional[list[int]]:
    """Return None for global roles (Admin / Supervisor Utama), else explicit region IDs."""
    if user.role and user.role.code in GLOBAL_SCOPE_ROLES:
        return None
    return sorted({ur.region_id for ur in (user.user_regions or [])})


def ensure_region_in_scope(user: User, region_id: int) -> None:
    """Raise 403 Forbidden if a scoped user attempts cross-region access (TC-02)."""
    allowed = resolve_user_region_ids(user)
    if allowed is None:
        return
    if region_id not in allowed:
        raise ForbiddenError("Akses ditolak: wilayah berada di luar cakupan tugas Anda.")


def _extract_bearer_token() -> str:
    auth_header = (request.headers.get("Authorization") or "").strip()
    if not auth_header.lower().startswith("bearer "):
        raise UnauthorizedError("Header Authorization Bearer token tidak ditemukan.")
    token = auth_header[7:].strip()
    if not token:
        raise UnauthorizedError("Token Bearer kosong.")
    return token


def authenticate_request_user() -> User:
    """Validate Bearer JWT access token, verify active account, and load User."""
    token = _extract_bearer_token()
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("Access token telah kedaluwarsa.") from exc
    except jwt.InvalidTokenError as exc:
        raise UnauthorizedError("Access token tidak valid.") from exc

    if payload.get("type") != "access":
        raise UnauthorizedError("Tipe token tidak valid untuk akses endpoint ini.")

    sub = payload.get("sub")
    try:
        user_id = int(sub)
    except (TypeError, ValueError) as exc:
        raise UnauthorizedError("Subject token tidak valid.") from exc

    user = db.session.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise UnauthorizedError("Akun pengguna tidak ditemukan.")
    if not user.is_active:
        raise ForbiddenError("Akun Anda sedang dinonaktifkan. Hubungi Admin.")

    g.current_user = user
    return user


def require_auth(view_func):
    """Decorator enforcing valid active JWT access token."""

    @wraps(view_func)
    def wrapped(*args, **kwargs):
        authenticate_request_user()
        return view_func(*args, **kwargs)

    return wrapped


def require_permission(permission: str):
    """Decorator enforcing authentication + active account + explicit role permission."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            user = authenticate_request_user()
            if not user_has_permission(user, permission):
                raise ForbiddenError(
                    f"Role '{user.role.code}' tidak memiliki izin '{permission}'."
                )
            return view_func(*args, **kwargs)

        return wrapped

    return decorator
