"""Auto-discovery and upsert sync from PRTG telemetry into PostgreSQL (ADR-005)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.prtg.normalizer import normalize_prtg_status, parse_prtg_duration_hours
from app.modules.regions.models import Region
from app.modules.schools.models import PrtgDevice, PrtgSensor, School
from app.modules.schools.normalizer import (
    display_school_name,
    extract_ipv4,
    extract_school_code,
    infer_diy_region_code,
    is_school_device,
    normalize_school_name,
)


@dataclass(frozen=True)
class SchoolSyncSummary:
    sensors_seen: int
    school_sensors_synced: int
    schools_created: int
    devices_created: int
    sensors_created: int
    checked_at: datetime


class SchoolSyncService:
    """Syncs school PRTG devices and sensors into PostgreSQL without losing Admin mappings."""

    def sync_snapshot(
        self,
        session: Session,
        sensors: Iterable[dict],
        *,
        checked_at: Optional[datetime] = None,
    ) -> SchoolSyncSummary:
        ts = checked_at or datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        regions_by_code = {
            r.code: r for r in session.scalars(select(Region)).all()
        }
        if not regions_by_code:
            raise RuntimeError("Tabel regions kosong. Jalankan seed_baseline_data terlebih dahulu.")

        fallback_region = regions_by_code.get("belum_dikelompokkan") or next(
            iter(regions_by_code.values())
        )

        # Group incoming school sensors by PRTG parent device ID
        sensors_list = [s for s in sensors if isinstance(s, dict)]
        grouped_by_device: dict[str, list[dict]] = {}
        for sensor in sensors_list:
            device_name = str(sensor.get("device") or "").strip()
            if not is_school_device(device_name):
                continue
            parent_id = str(sensor.get("parentid") or "").strip()
            if not parent_id:
                parent_id = f"obj-{sensor.get('objid', 'unknown')}"
            grouped_by_device.setdefault(parent_id, []).append(sensor)

        schools_created = 0
        devices_created = 0
        sensors_created = 0
        school_sensors_synced = 0

        for prtg_device_id, device_sensors in grouped_by_device.items():
            first = device_sensors[0]
            raw_device_name = str(first.get("device") or "").strip()
            group_path = str(first.get("group") or "").strip()
            sensor_names = " ".join(str(s.get("sensor") or "") for s in device_sensors)
            ip_addr = extract_ipv4(raw_device_name)

            # 1. Resolve existing PrtgDevice first (preserves Admin re-assignments!)
            device = session.scalar(
                select(PrtgDevice).where(PrtgDevice.prtg_device_id == prtg_device_id)
            )
            if device is None:
                region_code = infer_diy_region_code(group_path, raw_device_name, sensor_names)
                region = regions_by_code.get(region_code, fallback_region)
                norm_name = normalize_school_name(raw_device_name)
                school_code = extract_school_code(raw_device_name)

                # Match existing School by (normalized_name, region_id) for multi-device grouping
                school = session.scalar(
                    select(School).where(
                        School.normalized_name == norm_name,
                        School.region_id == region.id,
                    )
                )
                if school is None and school_code:
                    school = session.scalar(
                        select(School).where(School.school_code == school_code)
                    )
                if school is None:
                    # Also check unique normalized_name if previously assigned to fallback region
                    school = session.scalar(
                        select(School).where(School.normalized_name == norm_name)
                    )

                if school is None:
                    school = School(
                        school_code=school_code,
                        name=display_school_name(raw_device_name),
                        normalized_name=norm_name,
                        region_id=region.id,
                        is_active=True,
                    )
                    session.add(school)
                    session.flush()
                    schools_created += 1
                elif school_code and not school.school_code:
                    # Only attach code if not already used by another school
                    used = session.scalar(
                        select(School.id).where(School.school_code == school_code)
                    )
                    if used is None:
                        school.school_code = school_code

                device = PrtgDevice(
                    prtg_device_id=prtg_device_id,
                    school_id=school.id,
                    raw_name=raw_device_name,
                    ip_address=ip_addr,
                    prtg_group_path=group_path or None,
                    last_seen_at=ts,
                )
                session.add(device)
                session.flush()
                devices_created += 1
            else:
                device.raw_name = raw_device_name or device.raw_name
                if ip_addr:
                    device.ip_address = ip_addr
                if group_path:
                    device.prtg_group_path = group_path
                device.last_seen_at = ts

            # 2. Upsert sensors under this device
            for s in device_sensors:
                prtg_sensor_id = str(s.get("objid") or "").strip()
                if not prtg_sensor_id:
                    continue

                raw_status = str(s.get("status") or "").strip()
                raw_code = s.get("raw_status_code", s.get("status_raw"))
                try:
                    status_code = (
                        int(raw_code)
                        if raw_code is not None and str(raw_code).strip() != ""
                        else None
                    )
                except ValueError:
                    status_code = None

                normalized = s.get("normalized_status") or normalize_prtg_status(
                    raw_status, status_code
                )
                downtime_hours = s.get("downtime_hours")
                if downtime_hours is None:
                    downtime_hours = parse_prtg_duration_hours(
                        s.get("downtimesince"), s.get("downtimesince_raw")
                    )
                uptime_hours = s.get("uptime_hours")
                if uptime_hours is None:
                    uptime_hours = parse_prtg_duration_hours(s.get("uptimesince"))

                sensor_row = session.scalar(
                    select(PrtgSensor).where(PrtgSensor.prtg_sensor_id == prtg_sensor_id)
                )
                if sensor_row is None:
                    sensor_row = PrtgSensor(
                        prtg_sensor_id=prtg_sensor_id,
                        prtg_device_id=device.id,
                        sensor_name=str(s.get("sensor") or "Unknown Sensor").strip(),
                        raw_status=raw_status,
                        raw_status_code=status_code,
                        normalized_status=normalized,
                        downtime_hours=downtime_hours,
                        uptime_hours=uptime_hours,
                        last_message=str(s.get("message") or "").strip() or None,
                        last_checked_at=ts,
                    )
                    session.add(sensor_row)
                    sensors_created += 1
                else:
                    sensor_row.prtg_device_id = device.id
                    sensor_row.sensor_name = str(s.get("sensor") or sensor_row.sensor_name).strip()
                    sensor_row.raw_status = raw_status
                    sensor_row.raw_status_code = status_code
                    sensor_row.normalized_status = normalized
                    sensor_row.downtime_hours = downtime_hours
                    sensor_row.uptime_hours = uptime_hours
                    sensor_row.last_message = str(s.get("message") or "").strip() or None
                    sensor_row.last_checked_at = ts

                school_sensors_synced += 1

        session.commit()
        return SchoolSyncSummary(
            sensors_seen=len(sensors_list),
            school_sensors_synced=school_sensors_synced,
            schools_created=schools_created,
            devices_created=devices_created,
            sensors_created=sensors_created,
            checked_at=ts,
        )
