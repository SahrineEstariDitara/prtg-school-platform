"""Region administration service (FR-023, FR-026)."""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.audit import record_audit_log
from app.common.errors import ConflictError, NotFoundError, ValidationError
from app.modules.regions.models import Region


def _serialize_region(reg: Region) -> dict:
    return {
        "id": reg.id,
        "code": reg.code,
        "name": reg.name,
        "is_fallback": reg.is_fallback,
        "is_active": reg.is_active,
    }


def list_regions(
    session: Session,
    *,
    allowed_region_ids: Optional[Iterable[int]] = None,
    include_inactive: bool = False,
) -> list[dict]:
    stmt = select(Region).order_by(Region.is_fallback.asc(), Region.name.asc())
    if not include_inactive:
        stmt = stmt.where(Region.is_active.is_(True))
    if allowed_region_ids is not None:
        r_list = list(allowed_region_ids)
        if not r_list:
            return []
        stmt = stmt.where(Region.id.in_(r_list))
    return [_serialize_region(r) for r in session.scalars(stmt).all()]


def create_region(session: Session, payload: dict[str, Any], *, actor_id: int) -> dict:
    name = str(payload.get("name") or "").strip()
    raw_code = str(payload.get("code") or "").strip().lower()
    if not name:
        raise ValidationError("Nama wilayah wajib diisi.")

    code = raw_code or re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    if not code:
        raise ValidationError("Kode wilayah tidak valid.")

    if session.scalar(select(Region.id).where(Region.code == code)) is not None:
        raise ConflictError(f"Kode wilayah '{code}' sudah ada.")
    if session.scalar(select(Region.id).where(Region.name == name)) is not None:
        raise ConflictError(f"Nama wilayah '{name}' sudah ada.")

    region = Region(
        code=code,
        name=name,
        is_fallback=False,
        is_active=bool(payload.get("is_active", True)),
    )
    session.add(region)
    session.flush()

    serialized = _serialize_region(region)
    record_audit_log(
        session,
        actor_id=actor_id,
        action="region.create",
        entity_type="region",
        entity_id=region.id,
        before=None,
        after=serialized,
    )
    session.commit()
    return serialized


def update_region(
    session: Session,
    region_id: int,
    payload: dict[str, Any],
    *,
    actor_id: int,
) -> dict:
    region = session.scalar(select(Region).where(Region.id == region_id))
    if region is None:
        raise NotFoundError("Wilayah tidak ditemukan.")

    before = _serialize_region(region)

    if "name" in payload:
        name = str(payload["name"] or "").strip()
        if not name:
            raise ValidationError("Nama wilayah tidak boleh kosong.")
        conflict = session.scalar(
            select(Region.id).where(Region.name == name, Region.id != region.id)
        )
        if conflict is not None:
            raise ConflictError(f"Nama wilayah '{name}' sudah digunakan.")
        region.name = name

    if "is_active" in payload:
        region.is_active = bool(payload["is_active"])

    session.flush()
    after = _serialize_region(region)
    record_audit_log(
        session,
        actor_id=actor_id,
        action="region.update",
        entity_type="region",
        entity_id=region.id,
        before=before,
        after=after,
    )
    session.commit()
    return after
