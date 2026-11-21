"""Shared MonitoringService computing school network status, freshness, and dashboard summary.

Strict separation of domains (FR-008, AC-04, NFR-05):
- `network_status` is derived strictly from PRTG telemetry (`online`/`offline`/`warning`/`unknown`).
- `is_stale` / `last_checked_at` track telemetry freshness independently (stale != online).
- `active_incident` comes exclusively from the `incidents` workflow table.
- Opsi A (approved M4): PRTG `Unusual` remains `raw_status="Unusual"` on sensor detail,
  but does NOT downgrade a healthy school to `warning` unless the sensor is explicitly listed
  as a required sensor in `SchoolRecoveryPolicy`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.integrations.prtg.normalizer import is_telemetry_stale
from app.modules.incidents.models import INCIDENT_RESOLVED, Incident
from app.modules.schools.models import (
    NETWORK_STATUS_OFFLINE,
    NETWORK_STATUS_ONLINE,
    NETWORK_STATUS_UNKNOWN,
    NETWORK_STATUS_WARNING,
    PrtgDevice,
    PrtgSensor,
    School,
    SchoolRecoveryPolicy,
)


def _effective_sensor_status_for_school(
    sensor: PrtgSensor,
    required_sensor_ids: set[int],
) -> str:
    """Apply Opsi A: non-required PRTG 'Unusual' sensors count as online for school aggregation."""
    status = sensor.normalized_status
    if status == NETWORK_STATUS_WARNING:
        raw_lower = (sensor.raw_status or "").strip().lower()
        if raw_lower.startswith("unusual") and sensor.id not in required_sensor_ids:
            return NETWORK_STATUS_ONLINE
    return status


def compute_school_network_status(
    sensors: Iterable[PrtgSensor],
    *,
    required_sensor_ids: Optional[set[int]] = None,
    now: Optional[datetime] = None,
    stale_threshold_seconds: Optional[int] = None,
) -> tuple[str, bool, Optional[datetime]]:
    """Aggregate normalized sensor statuses for one school into (network_status, is_stale, last_checked_at)."""
    sensor_list = list(sensors)
    req_ids = required_sensor_ids or set()
    threshold = (
        settings.prtg_stale_threshold_seconds
        if stale_threshold_seconds is None
        else int(stale_threshold_seconds)
    )

    if not sensor_list:
        return NETWORK_STATUS_UNKNOWN, True, None

    checked_times = [s.last_checked_at for s in sensor_list if s.last_checked_at is not None]
    last_checked = max(checked_times) if checked_times else None
    stale = is_telemetry_stale(last_checked, now=now, threshold_seconds=threshold)

    statuses = {_effective_sensor_status_for_school(s, req_ids) for s in sensor_list}
    if NETWORK_STATUS_OFFLINE in statuses:
        network_status = NETWORK_STATUS_OFFLINE
    elif NETWORK_STATUS_WARNING in statuses:
        network_status = NETWORK_STATUS_WARNING
    elif statuses == {NETWORK_STATUS_UNKNOWN}:
        network_status = NETWORK_STATUS_UNKNOWN
    elif NETWORK_STATUS_ONLINE in statuses:
        network_status = NETWORK_STATUS_ONLINE
    else:
        network_status = NETWORK_STATUS_UNKNOWN

    return network_status, stale, last_checked


def _iso_utc(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _school_required_sensor_ids(school: School) -> set[int]:
    policy = school.recovery_policy
    if policy is None:
        return set()
    return {rs.prtg_sensor_id for rs in policy.required_sensors}


class MonitoringService:
    """Shared read service for dashboard summary, school list, and school detail."""

    def __init__(self, *, stale_threshold_seconds: Optional[int] = None) -> None:
        self.stale_threshold_seconds = (
            settings.prtg_stale_threshold_seconds
            if stale_threshold_seconds is None
            else int(stale_threshold_seconds)
        )

    def _base_school_query(self):
        return (
            select(School)
            .where(School.is_active.is_(True))
            .options(
                selectinload(School.devices).selectinload(PrtgDevice.sensors),
                selectinload(School.recovery_policy).selectinload(
                    SchoolRecoveryPolicy.required_sensors
                ),
            )
        )

    def _load_active_incidents_map(
        self,
        session: Session,
        school_ids: list[int],
    ) -> dict[int, Incident]:
        if not school_ids:
            return {}
        stmt = select(Incident).where(
            Incident.school_id.in_(school_ids),
            Incident.incident_status != INCIDENT_RESOLVED,
        )
        return {inc.school_id: inc for inc in session.scalars(stmt).all()}

    def _serialize_school_row(
        self,
        school: School,
        active_incident: Optional[Incident],
        *,
        now: Optional[datetime] = None,
    ) -> dict:
        sensors = [s for dev in school.devices for s in dev.sensors]
        req_ids = _school_required_sensor_ids(school)
        net_status, stale, last_checked = compute_school_network_status(
            sensors,
            required_sensor_ids=req_ids,
            now=now,
            stale_threshold_seconds=self.stale_threshold_seconds,
        )
        return {
            "id": school.id,
            "school_code": school.school_code,
            "name": school.name,
            "region": {
                "id": school.region.id,
                "code": school.region.code,
                "name": school.region.name,
            },
            "network_status": net_status,
            "is_stale": stale,
            "last_checked_at": _iso_utc(last_checked),
            "device_count": len(school.devices),
            "sensor_count": len(sensors),
            "active_incident": (
                {
                    "id": active_incident.id,
                    "incident_status": active_incident.incident_status,
                    "current_assignee_id": active_incident.current_assignee_id,
                    "detected_at": _iso_utc(active_incident.detected_at),
                }
                if active_incident is not None
                else None
            ),
        }

    def get_dashboard_summary(
        self,
        session: Session,
        *,
        region_ids: Optional[Iterable[int]] = None,
        now: Optional[datetime] = None,
    ) -> dict:
        """Compute global or region-scoped monitoring summary from PostgreSQL."""
        stmt = self._base_school_query()
        if region_ids is not None:
            r_list = list(region_ids)
            if not r_list:
                return {
                    "total_schools": 0,
                    "counts": {"online": 0, "offline": 0, "warning": 0, "unknown": 0},
                    "stale_schools": 0,
                    "active_incidents": 0,
                    "last_checked_at": None,
                }
            stmt = stmt.where(School.region_id.in_(r_list))

        schools = session.scalars(stmt).all()
        active_map = self._load_active_incidents_map(session, [s.id for s in schools])

        counts = {
            NETWORK_STATUS_ONLINE: 0,
            NETWORK_STATUS_OFFLINE: 0,
            NETWORK_STATUS_WARNING: 0,
            NETWORK_STATUS_UNKNOWN: 0,
        }
        stale_schools = 0
        latest_check: Optional[datetime] = None

        for school in schools:
            sensors = [sn for dev in school.devices for sn in dev.sensors]
            net_status, stale, last_checked = compute_school_network_status(
                sensors,
                required_sensor_ids=_school_required_sensor_ids(school),
                now=now,
                stale_threshold_seconds=self.stale_threshold_seconds,
            )
            counts[net_status] = counts.get(net_status, 0) + 1
            if stale:
                stale_schools += 1
            if last_checked is not None and (latest_check is None or last_checked > latest_check):
                latest_check = last_checked

        return {
            "total_schools": len(schools),
            "counts": counts,
            "stale_schools": stale_schools,
            "active_incidents": len(active_map),
            "last_checked_at": _iso_utc(latest_check),
        }

    def get_region_summary(
        self,
        session: Session,
        *,
        region_ids: Optional[Iterable[int]] = None,
        now: Optional[datetime] = None,
    ) -> dict:
        """Return overall summary plus per-region breakdown (GET /api/v1/monitoring/summary)."""
        stmt = self._base_school_query()
        if region_ids is not None:
            r_list = list(region_ids)
            if not r_list:
                return {
                    "total_schools": 0,
                    "counts": {"online": 0, "offline": 0, "warning": 0, "unknown": 0},
                    "stale_schools": 0,
                    "active_incidents": 0,
                    "last_checked_at": None,
                    "by_region": [],
                }
            stmt = stmt.where(School.region_id.in_(r_list))

        schools = session.scalars(stmt).all()
        active_map = self._load_active_incidents_map(session, [s.id for s in schools])

        overall_counts = {
            NETWORK_STATUS_ONLINE: 0,
            NETWORK_STATUS_OFFLINE: 0,
            NETWORK_STATUS_WARNING: 0,
            NETWORK_STATUS_UNKNOWN: 0,
        }
        stale_schools = 0
        latest_check: Optional[datetime] = None
        by_region: dict[int, dict] = {}

        for school in schools:
            sensors = [sn for dev in school.devices for sn in dev.sensors]
            net_status, stale, last_checked = compute_school_network_status(
                sensors,
                required_sensor_ids=_school_required_sensor_ids(school),
                now=now,
                stale_threshold_seconds=self.stale_threshold_seconds,
            )
            overall_counts[net_status] += 1
            if stale:
                stale_schools += 1
            if last_checked is not None and (latest_check is None or last_checked > latest_check):
                latest_check = last_checked

            bucket = by_region.setdefault(
                school.region.id,
                {
                    "region": {
                        "id": school.region.id,
                        "code": school.region.code,
                        "name": school.region.name,
                    },
                    "total_schools": 0,
                    "counts": {"online": 0, "offline": 0, "warning": 0, "unknown": 0},
                    "stale_schools": 0,
                    "active_incidents": 0,
                },
            )
            bucket["total_schools"] += 1
            bucket["counts"][net_status] += 1
            if stale:
                bucket["stale_schools"] += 1
            if school.id in active_map:
                bucket["active_incidents"] += 1

        return {
            "total_schools": len(schools),
            "counts": overall_counts,
            "stale_schools": stale_schools,
            "active_incidents": len(active_map),
            "last_checked_at": _iso_utc(latest_check),
            "by_region": sorted(by_region.values(), key=lambda x: x["region"]["name"]),
        }

    def list_schools(
        self,
        session: Session,
        *,
        region_ids: Optional[Iterable[int]] = None,
        network_status: Optional[str] = None,
        query: Optional[str] = None,
        page: int = 1,
        per_page: int = 20,
        now: Optional[datetime] = None,
    ) -> dict:
        """Return paginated, filtered schools with separated network & incident state."""
        stmt = self._base_school_query().order_by(School.name.asc())
        if region_ids is not None:
            r_list = list(region_ids)
            if not r_list:
                return {"items": [], "pagination": {"page": page, "per_page": per_page, "total": 0}}
            stmt = stmt.where(School.region_id.in_(r_list))
        if query and query.strip():
            like_pattern = f"%{query.strip()}%"
            stmt = stmt.where(School.name.ilike(like_pattern))

        schools = session.scalars(stmt).all()
        active_map = self._load_active_incidents_map(session, [s.id for s in schools])

        rows = [
            self._serialize_school_row(s, active_map.get(s.id), now=now)
            for s in schools
        ]
        if network_status:
            target = network_status.strip().lower()
            rows = [r for r in rows if r["network_status"] == target]

        total = len(rows)
        page_safe = max(int(page), 1)
        per_page_safe = max(min(int(per_page), 200), 1)
        start = (page_safe - 1) * per_page_safe
        end = start + per_page_safe

        return {
            "items": rows[start:end],
            "pagination": {
                "page": page_safe,
                "per_page": per_page_safe,
                "total": total,
            },
        }

    def get_school_detail(
        self,
        session: Session,
        school_id: int,
        *,
        region_ids: Optional[Iterable[int]] = None,
        now: Optional[datetime] = None,
    ) -> Optional[dict]:
        """Return full school monitoring detail including devices, sensors, and recovery policy."""
        stmt = (
            select(School)
            .where(School.id == school_id)
            .options(
                selectinload(School.devices).selectinload(PrtgDevice.sensors),
                selectinload(School.recovery_policy).selectinload(
                    SchoolRecoveryPolicy.required_sensors
                ),
            )
        )
        if region_ids is not None:
            r_list = list(region_ids)
            if not r_list:
                return None
            stmt = stmt.where(School.region_id.in_(r_list))

        school = session.scalar(stmt)
        if school is None:
            return None

        active_map = self._load_active_incidents_map(session, [school.id])
        base = self._serialize_school_row(school, active_map.get(school.id), now=now)

        policy = school.recovery_policy
        required_sensor_ids = _school_required_sensor_ids(school)

        devices_payload = []
        for dev in school.devices:
            sensors_payload = [
                {
                    "id": sn.id,
                    "prtg_sensor_id": sn.prtg_sensor_id,
                    "sensor_name": sn.sensor_name,
                    "raw_status": sn.raw_status,
                    "normalized_status": sn.normalized_status,
                    "is_required": sn.id in required_sensor_ids,
                    "downtime_hours": sn.downtime_hours,
                    "uptime_hours": sn.uptime_hours,
                    "last_message": sn.last_message,
                    "last_checked_at": _iso_utc(sn.last_checked_at),
                    "is_stale": is_telemetry_stale(
                        sn.last_checked_at,
                        now=now,
                        threshold_seconds=self.stale_threshold_seconds,
                    ),
                }
                for sn in dev.sensors
            ]
            devices_payload.append(
                {
                    "id": dev.id,
                    "prtg_device_id": dev.prtg_device_id,
                    "raw_name": dev.raw_name,
                    "ip_address": dev.ip_address,
                    "prtg_group_path": dev.prtg_group_path,
                    "last_seen_at": _iso_utc(dev.last_seen_at),
                    "sensors": sensors_payload,
                }
            )

        base["devices"] = devices_payload
        base["recovery_policy"] = (
            {
                "version": policy.version,
                "required_consecutive_normal": policy.required_consecutive_normal,
                "recheck_interval_seconds": policy.recheck_interval_seconds,
                "required_sensor_ids": sorted(required_sensor_ids),
            }
            if policy is not None
            else None
        )
        return base
