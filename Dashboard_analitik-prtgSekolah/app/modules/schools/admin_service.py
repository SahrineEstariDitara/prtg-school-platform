"""Admin management for schools, multi-device mapping, and recovery policies (ADR-005, ADR-009)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.common.audit import record_audit_log
from app.common.errors import ConflictError, NotFoundError, ValidationError
from app.modules.regions.models import Region
from app.modules.schools.models import (
    NETWORK_STATUS_ONLINE,
    NETWORK_STATUS_WARNING,
    PrtgDevice,
    PrtgSensor,
    School,
    SchoolRecoveryPolicy,
    SchoolRecoveryPolicySensor,
)
from app.modules.schools.normalizer import normalize_school_name


def _serialize_policy(policy: SchoolRecoveryPolicy | None, school_id: int) -> dict:
    if policy is None:
        return {
            "school_id": school_id,
            "configured": False,
            "version": 0,
            "required_consecutive_normal": 3,
            "recheck_interval_seconds": 60,
            "required_sensors": [],
        }
    return {
        "school_id": school_id,
        "configured": len(policy.required_sensors) > 0,
        "version": policy.version,
        "required_consecutive_normal": policy.required_consecutive_normal,
        "recheck_interval_seconds": policy.recheck_interval_seconds,
        "required_sensors": [
            {
                "sensor_id": rs.prtg_sensor_id,
                "prtg_sensor_id": rs.sensor.prtg_sensor_id if rs.sensor else None,
                "sensor_name": rs.sensor.sensor_name if rs.sensor else None,
                "expected_status": rs.expected_status,
            }
            for rs in sorted(policy.required_sensors, key=lambda item: item.prtg_sensor_id)
        ],
    }


def update_school(
    session: Session,
    school_id: int,
    payload: dict[str, Any],
    *,
    actor_id: int,
) -> dict:
    """Update school metadata, region, active flag, or reassign PRTG devices (merge)."""
    school = session.scalar(
        select(School)
        .where(School.id == school_id)
        .options(selectinload(School.devices))
    )
    if school is None:
        raise NotFoundError("Sekolah tidak ditemukan.")

    before = {
        "name": school.name,
        "school_code": school.school_code,
        "region_id": school.region_id,
        "is_active": school.is_active,
        "prtg_device_ids": sorted(d.id for d in school.devices),
    }

    if "name" in payload:
        name = str(payload["name"] or "").strip()
        if not name:
            raise ValidationError("Nama sekolah tidak boleh kosong.")
        school.name = name
        school.normalized_name = normalize_school_name(name)

    if "school_code" in payload:
        code = str(payload["school_code"] or "").strip() or None
        if code and code != school.school_code:
            exists = session.scalar(
                select(School.id).where(School.school_code == code, School.id != school.id)
            )
            if exists is not None:
                raise ConflictError(f"Kode sekolah '{code}' sudah digunakan.")
        school.school_code = code

    if "region_id" in payload:
        try:
            rid = int(payload["region_id"])
        except (TypeError, ValueError) as exc:
            raise ValidationError("region_id harus berupa integer.") from exc
        region = session.scalar(select(Region).where(Region.id == rid, Region.is_active.is_(True)))
        if region is None:
            raise ValidationError("Wilayah tujuan tidak ditemukan atau tidak aktif.")
        school.region_id = region.id

    if "is_active" in payload:
        school.is_active = bool(payload["is_active"])

    if "attach_prtg_device_ids" in payload:
        try:
            device_ids = [int(x) for x in (payload["attach_prtg_device_ids"] or [])]
        except (TypeError, ValueError) as exc:
            raise ValidationError("attach_prtg_device_ids harus berupa daftar integer.") from exc

        if device_ids:
            devices = session.scalars(
                select(PrtgDevice).where(PrtgDevice.id.in_(device_ids))
            ).all()
            if len(devices) != len(set(device_ids)):
                raise ValidationError("Satu atau lebih prtg_device tidak ditemukan.")
            for dev in devices:
                dev.school_id = school.id

    session.flush()
    session.refresh(school)

    after = {
        "name": school.name,
        "school_code": school.school_code,
        "region_id": school.region_id,
        "is_active": school.is_active,
        "prtg_device_ids": sorted(d.id for d in school.devices),
    }
    record_audit_log(
        session,
        actor_id=actor_id,
        action="school.update",
        entity_type="school",
        entity_id=school.id,
        before=before,
        after=after,
    )
    session.commit()
    return {"id": school.id, **after}


def get_recovery_policy(session: Session, school_id: int) -> dict:
    school = session.scalar(
        select(School)
        .where(School.id == school_id)
        .options(
            selectinload(School.recovery_policy)
            .selectinload(SchoolRecoveryPolicy.required_sensors)
            .selectinload(SchoolRecoveryPolicySensor.sensor)
        )
    )
    if school is None:
        raise NotFoundError("Sekolah tidak ditemukan.")
    return _serialize_policy(school.recovery_policy, school.id)


def upsert_recovery_policy(
    session: Session,
    school_id: int,
    payload: dict[str, Any],
    *,
    actor_id: int,
) -> dict:
    """Create or update required sensors & consecutive stability checks for a school (ADR-009)."""
    school = session.scalar(
        select(School)
        .where(School.id == school_id)
        .options(
            selectinload(School.devices).selectinload(PrtgDevice.sensors),
            selectinload(School.recovery_policy)
            .selectinload(SchoolRecoveryPolicy.required_sensors)
            .selectinload(SchoolRecoveryPolicySensor.sensor),
        )
    )
    if school is None:
        raise NotFoundError("Sekolah tidak ditemukan.")

    raw_sensor_ids = payload.get("required_sensor_ids")
    if not isinstance(raw_sensor_ids, list) or len(raw_sensor_ids) == 0:
        raise ValidationError("required_sensor_ids wajib berisi minimal 1 ID sensor milik sekolah ini.")

    try:
        sensor_ids = list(dict.fromkeys(int(x) for x in raw_sensor_ids))
    except (TypeError, ValueError) as exc:
        raise ValidationError("required_sensor_ids harus berupa daftar integer.") from exc

    school_sensor_map: dict[int, PrtgSensor] = {
        sn.id: sn for dev in school.devices for sn in dev.sensors
    }
    invalid_sensors = [sid for sid in sensor_ids if sid not in school_sensor_map]
    if invalid_sensors:
        raise ValidationError(
            "Semua sensor wajib harus berasal dari perangkat milik sekolah tersebut.",
            details=[{"field": "required_sensor_ids", "invalid_sensor_ids": invalid_sensors}],
        )

    try:
        consecutive = int(payload.get("required_consecutive_normal", 3))
        interval = int(payload.get("recheck_interval_seconds", 60))
    except (TypeError, ValueError) as exc:
        raise ValidationError("required_consecutive_normal dan recheck_interval_seconds harus angka.") from exc

    if consecutive < 1 or consecutive > 20:
        raise ValidationError("required_consecutive_normal harus antara 1 sampai 20.")
    if interval < 10 or interval > 3600:
        raise ValidationError("recheck_interval_seconds harus antara 10 sampai 3600 detik.")

    expected_status = str(payload.get("expected_status") or NETWORK_STATUS_ONLINE).strip().lower()
    if expected_status not in (NETWORK_STATUS_ONLINE, NETWORK_STATUS_WARNING):
        raise ValidationError("expected_status hanya boleh 'online' atau 'warning'.")

    before = _serialize_policy(school.recovery_policy, school.id)

    policy = school.recovery_policy
    if policy is None:
        policy = SchoolRecoveryPolicy(
            school_id=school.id,
            version=1,
            required_consecutive_normal=consecutive,
            recheck_interval_seconds=interval,
            updated_by_id=actor_id,
        )
        session.add(policy)
        session.flush()
    else:
        policy.version += 1
        policy.required_consecutive_normal = consecutive
        policy.recheck_interval_seconds = interval
        policy.updated_by_id = actor_id
        policy.required_sensors.clear()
        session.flush()

    for sid in sensor_ids:
        policy.required_sensors.append(
            SchoolRecoveryPolicySensor(
                policy_id=policy.id,
                prtg_sensor_id=sid,
                expected_status=expected_status,
            )
        )

    session.flush()
    session.refresh(policy)
    after = _serialize_policy(policy, school.id)

    record_audit_log(
        session,
        actor_id=actor_id,
        action="school_recovery_policy.upsert",
        entity_type="school_recovery_policy",
        entity_id=policy.id,
        before=before,
        after=after,
    )
    session.commit()
    return after
