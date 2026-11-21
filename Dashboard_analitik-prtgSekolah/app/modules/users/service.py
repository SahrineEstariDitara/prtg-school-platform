"""User administration service enforcing 5 fixed roles & ADR-012 multi-region rules (FR-021, FR-022)."""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.security import generate_password_hash

from app.common.audit import record_audit_log
from app.common.errors import ConflictError, NotFoundError, ValidationError
from app.common.rbac import GLOBAL_SCOPE_ROLES, MULTI_REGION_ROLES, SINGLE_REGION_ROLES
from app.modules.auth.models import FIXED_ROLE_CODES, Role
from app.modules.auth.service import serialize_user_profile
from app.modules.regions.models import Region, UserRegion
from app.modules.users.models import User


def _validate_and_load_regions(session: Session, role_code: str, region_ids: list[int]) -> list[Region]:
    unique_ids = list(dict.fromkeys(int(rid) for rid in region_ids))

    if role_code in SINGLE_REGION_ROLES and len(unique_ids) != 1:
        raise ValidationError(
            f"Role '{role_code}' wajib memiliki tepat 1 wilayah kerja (ADR-012).",
            details=[{"field": "region_ids", "expected_count": 1, "actual_count": len(unique_ids)}],
        )

    if role_code in MULTI_REGION_ROLES and len(unique_ids) < 1:
        raise ValidationError(
            f"Role '{role_code}' wajib memiliki minimal 1 wilayah kerja (ADR-012).",
            details=[{"field": "region_ids", "expected_min": 1, "actual_count": 0}],
        )

    if role_code in GLOBAL_SCOPE_ROLES:
        # Global roles do not require region bindings
        if not unique_ids:
            return []

    if not unique_ids:
        return []

    regions = session.scalars(
        select(Region).where(Region.id.in_(unique_ids), Region.is_active.is_(True))
    ).all()
    if len(regions) != len(unique_ids):
        found_ids = {r.id for r in regions}
        missing = [rid for rid in unique_ids if rid not in found_ids]
        raise ValidationError(
            "Satu atau lebih region_ids tidak ditemukan atau tidak aktif.",
            details=[{"field": "region_ids", "invalid_ids": missing}],
        )
    return list(regions)


def list_users(
    session: Session,
    *,
    role_code: Optional[str] = None,
    region_id: Optional[int] = None,
    is_active: Optional[bool] = None,
    query: Optional[str] = None,
) -> list[dict]:
    stmt = select(User).order_by(User.id.asc())
    if is_active is not None:
        stmt = stmt.where(User.is_active.is_(is_active))
    if query and query.strip():
        pattern = f"%{query.strip()}%"
        stmt = stmt.where((User.username.ilike(pattern)) | (User.full_name.ilike(pattern)))

    users = session.scalars(stmt).all()
    results = []
    for u in users:
        if role_code and (u.role is None or u.role.code != role_code):
            continue
        if region_id is not None:
            u_rids = {ur.region_id for ur in (u.user_regions or [])}
            if region_id not in u_rids:
                continue
        results.append(serialize_user_profile(u))
    return results


def create_user(session: Session, payload: dict[str, Any], *, actor_id: int) -> dict:
    username = str(payload.get("username") or "").strip()
    full_name = str(payload.get("full_name") or "").strip()
    password = str(payload.get("password") or "")
    email = str(payload.get("email") or "").strip() or None
    role_code = str(payload.get("role_code") or "").strip()
    raw_region_ids = payload.get("region_ids")
    is_active = bool(payload.get("is_active", True))
    is_on_duty = bool(payload.get("is_on_duty", False))

    if not username or len(username) < 3:
        raise ValidationError("Username wajib diisi minimal 3 karakter.")
    if not full_name:
        raise ValidationError("full_name wajib diisi.")
    if len(password) < 6:
        raise ValidationError("Password wajib diisi minimal 6 karakter.")
    if role_code not in FIXED_ROLE_CODES:
        raise ValidationError(
            "role_code harus salah satu dari lima role tetap.",
            details=[{"field": "role_code", "allowed": list(FIXED_ROLE_CODES)}],
        )

    if session.scalar(select(User.id).where(User.username == username)) is not None:
        raise ConflictError(f"Username '{username}' sudah digunakan.")
    if email and session.scalar(select(User.id).where(User.email == email)) is not None:
        raise ConflictError(f"Email '{email}' sudah digunakan.")

    role = session.scalar(select(Role).where(Role.code == role_code))
    if role is None:
        raise ValidationError(f"Role '{role_code}' belum tersedia di database.")

    try:
        region_ids = [int(x) for x in (raw_region_ids or [])]
    except (TypeError, ValueError) as exc:
        raise ValidationError("region_ids harus berupa daftar integer.") from exc

    regions = _validate_and_load_regions(session, role_code, region_ids)

    user = User(
        username=username,
        email=email,
        full_name=full_name,
        password_hash=generate_password_hash(password),
        role_id=role.id,
        is_active=is_active,
        is_on_duty=is_on_duty,
    )
    session.add(user)
    session.flush()

    for reg in regions:
        session.add(UserRegion(user_id=user.id, region_id=reg.id))
    session.flush()
    session.refresh(user)

    serialized = serialize_user_profile(user)
    record_audit_log(
        session,
        actor_id=actor_id,
        action="user.create",
        entity_type="user",
        entity_id=user.id,
        before=None,
        after=serialized,
    )
    session.commit()
    return serialized


def update_user(
    session: Session,
    user_id: int,
    payload: dict[str, Any],
    *,
    actor_id: int,
) -> dict:
    user = session.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise NotFoundError("Pengguna tidak ditemukan.")

    before_state = serialize_user_profile(user)

    if "full_name" in payload:
        name = str(payload["full_name"] or "").strip()
        if not name:
            raise ValidationError("full_name tidak boleh kosong.")
        user.full_name = name

    if "email" in payload:
        new_email = str(payload["email"] or "").strip() or None
        if new_email and new_email != user.email:
            exists = session.scalar(
                select(User.id).where(User.email == new_email, User.id != user.id)
            )
            if exists is not None:
                raise ConflictError(f"Email '{new_email}' sudah digunakan.")
        user.email = new_email

    if "password" in payload and payload["password"]:
        pwd = str(payload["password"])
        if len(pwd) < 6:
            raise ValidationError("Password minimal 6 karakter.")
        user.password_hash = generate_password_hash(pwd)

    if "is_active" in payload:
        user.is_active = bool(payload["is_active"])

    if "is_on_duty" in payload:
        user.is_on_duty = bool(payload["is_on_duty"])

    target_role_code = user.role.code
    if "role_code" in payload:
        new_role_code = str(payload["role_code"] or "").strip()
        if new_role_code not in FIXED_ROLE_CODES:
            raise ValidationError(
                "role_code harus salah satu dari lima role tetap.",
                details=[{"field": "role_code", "allowed": list(FIXED_ROLE_CODES)}],
            )
        role = session.scalar(select(Role).where(Role.code == new_role_code))
        if role is None:
            raise ValidationError(f"Role '{new_role_code}' tidak ditemukan.")
        user.role_id = role.id
        user.role = role
        target_role_code = new_role_code

    if "region_ids" in payload or "role_code" in payload:
        if "region_ids" in payload:
            try:
                target_region_ids = [int(x) for x in (payload["region_ids"] or [])]
            except (TypeError, ValueError) as exc:
                raise ValidationError("region_ids harus berupa daftar integer.") from exc
        else:
            target_region_ids = [ur.region_id for ur in (user.user_regions or [])]

        regions = _validate_and_load_regions(session, target_role_code, target_region_ids)
        # Replace user_regions cleanly
        user.user_regions.clear()
        session.flush()
        for reg in regions:
            user.user_regions.append(UserRegion(user_id=user.id, region_id=reg.id))

    session.flush()
    session.refresh(user)
    after_state = serialize_user_profile(user)

    record_audit_log(
        session,
        actor_id=actor_id,
        action="user.update",
        entity_type="user",
        entity_id=user.id,
        before=before_state,
        after=after_state,
    )
    session.commit()
    return after_state
