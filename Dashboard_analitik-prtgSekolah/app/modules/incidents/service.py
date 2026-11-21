"""Incident correlation, deduplication, and scoped query service (FR-007, FR-020, ADR-007)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.common.errors import NotFoundError
from app.common.rbac import ensure_region_in_scope, resolve_user_region_ids
from app.infrastructure.database.extensions import utcnow
from app.infrastructure.push import FCMAdapter
from app.modules.assignments.models import IncidentAssignment
from app.modules.assignments.service import auto_assign_incident
from app.modules.incidents.history import record_incident_history
from app.modules.incidents.models import (
    INCIDENT_DETECTED,
    INCIDENT_RESOLVED,
    Incident,
    IncidentHistory,
    IncidentSensor,
)
from app.modules.monitoring.service import MonitoringService
from app.modules.responses.models import ResponseLog
from app.modules.schools.models import (
    NETWORK_STATUS_OFFLINE,
    PrtgDevice,
    School,
    SchoolRecoveryPolicy,
)
from app.modules.users.models import User
from app.modules.verification.models import PrtgRecheckLog, VerificationRecord


def _iso_utc(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class CorrelationSummary:
    schools_offline_seen: int
    incidents_created: int
    incidents_updated: int
    sensors_linked: int


def correlate_down_schools(
    session: Session,
    *,
    now: Optional[datetime] = None,
    push_adapter: Optional[FCMAdapter] = None,
) -> CorrelationSummary:
    """Correlate offline school sensors into exactly 1 active incident per school (FR-007, TC-05)."""
    ts = now or utcnow()
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    schools = session.scalars(
        select(School)
        .where(School.is_active.is_(True))
        .options(selectinload(School.devices).selectinload(PrtgDevice.sensors))
    ).all()

    schools_offline_seen = 0
    incidents_created = 0
    incidents_updated = 0
    sensors_linked = 0

    for school in schools:
        offline_sensors = [
            sn
            for dev in school.devices
            for sn in dev.sensors
            if sn.normalized_status == NETWORK_STATUS_OFFLINE
        ]
        if not offline_sensors:
            continue

        schools_offline_seen += 1

        active_inc = session.scalar(
            select(Incident).where(
                Incident.school_id == school.id,
                Incident.incident_status != INCIDENT_RESOLVED,
            )
        )

        is_new = False
        if active_inc is None:
            active_inc = Incident(
                school_id=school.id,
                incident_status=INCIDENT_DETECTED,
                detected_at=ts,
            )
            session.add(active_inc)
            try:
                session.flush()
                is_new = True
                incidents_created += 1
            except IntegrityError:
                session.rollback()
                active_inc = session.scalar(
                    select(Incident).where(
                        Incident.school_id == school.id,
                        Incident.incident_status != INCIDENT_RESOLVED,
                    )
                )
                assert active_inc is not None
                incidents_updated += 1
        else:
            incidents_updated += 1

        existing_links = {
            link.prtg_sensor_id: link
            for link in session.scalars(
                select(IncidentSensor).where(IncidentSensor.incident_id == active_inc.id)
            ).all()
        }
        newly_linked_ids = []
        for sn in offline_sensors:
            if sn.id in existing_links:
                existing_links[sn.id].last_seen_down_at = ts
            else:
                session.add(
                    IncidentSensor(
                        incident_id=active_inc.id,
                        prtg_sensor_id=sn.id,
                        first_detected_at=ts,
                        last_seen_down_at=ts,
                    )
                )
                newly_linked_ids.append(sn.id)
                sensors_linked += 1

        if is_new:
            record_incident_history(
                session,
                incident_id=active_inc.id,
                event_type="incident.detected",
                from_status=None,
                to_status=INCIDENT_DETECTED,
                metadata={
                    "school_id": school.id,
                    "sensor_ids": [sn.id for sn in offline_sensors],
                },
            )
            session.flush()
            auto_assign_incident(
                session,
                active_inc.id,
                push_adapter=push_adapter,
                commit=False,
            )
        elif newly_linked_ids:
            record_incident_history(
                session,
                incident_id=active_inc.id,
                event_type="incident.sensors_added",
                from_status=active_inc.incident_status,
                to_status=active_inc.incident_status,
                metadata={"added_sensor_ids": newly_linked_ids},
            )

    session.commit()
    return CorrelationSummary(
        schools_offline_seen=schools_offline_seen,
        incidents_created=incidents_created,
        incidents_updated=incidents_updated,
        sensors_linked=sensors_linked,
    )


def _serialize_incident_summary(inc: Incident, network_info: Optional[dict] = None) -> dict:
    return {
        "id": inc.id,
        "school": {
            "id": inc.school.id,
            "name": inc.school.name,
            "region": {
                "id": inc.school.region.id,
                "code": inc.school.region.code,
                "name": inc.school.region.name,
            },
        },
        "network_status": network_info["network_status"] if network_info else "unknown",
        "is_stale": network_info["is_stale"] if network_info else True,
        "last_checked_at": network_info["last_checked_at"] if network_info else None,
        "incident_status": inc.incident_status,
        "recovery_stable_streak": inc.recovery_stable_streak,
        "current_assignee": (
            {
                "id": inc.current_assignee.id,
                "username": inc.current_assignee.username,
                "full_name": inc.current_assignee.full_name,
            }
            if inc.current_assignee is not None
            else None
        ),
        "affected_sensor_count": len(inc.affected_sensors or []),
        "detected_at": _iso_utc(inc.detected_at),
        "acknowledged_at": _iso_utc(inc.acknowledged_at),
        "submitted_at": _iso_utc(inc.submitted_at),
        "resolved_at": _iso_utc(inc.resolved_at),
    }


def list_incidents(
    session: Session,
    user: User,
    *,
    region_id: Optional[int] = None,
    status: Optional[str] = None,
    school_id: Optional[int] = None,
    assigned_to_me: bool = False,
    page: int = 1,
    per_page: int = 20,
) -> dict:
    allowed = resolve_user_region_ids(user)
    if region_id is not None:
        ensure_region_in_scope(user, region_id)
        target_regions = [region_id]
    else:
        target_regions = allowed

    stmt = (
        select(Incident)
        .join(School, Incident.school_id == School.id)
        .options(
            selectinload(Incident.school).selectinload(School.region),
            selectinload(Incident.current_assignee),
            selectinload(Incident.affected_sensors),
        )
        .order_by(Incident.id.desc())
    )
    if target_regions is not None:
        if not target_regions:
            return {"items": [], "pagination": {"page": page, "per_page": per_page, "total": 0}}
        stmt = stmt.where(School.region_id.in_(target_regions))
    if status:
        stmt = stmt.where(Incident.incident_status == status.strip().lower())
    if school_id is not None:
        stmt = stmt.where(Incident.school_id == school_id)
    if assigned_to_me:
        stmt = stmt.where(Incident.current_assignee_id == user.id)

    rows = session.scalars(stmt).all()
    total = len(rows)
    page_safe = max(int(page), 1)
    per_page_safe = max(min(int(per_page), 100), 1)
    start = (page_safe - 1) * per_page_safe
    sliced = rows[start : start + per_page_safe]

    mon = MonitoringService()
    items = []
    for inc in sliced:
        sch_detail = mon.get_school_detail(session, inc.school_id)
        items.append(_serialize_incident_summary(inc, sch_detail))

    return {
        "items": items,
        "pagination": {"page": page_safe, "per_page": per_page_safe, "total": total},
    }


def get_incident_detail(session: Session, user: User, incident_id: int) -> dict:
    inc = session.scalar(
        select(Incident)
        .where(Incident.id == incident_id)
        .options(
            selectinload(Incident.school).selectinload(School.region),
            selectinload(Incident.school).selectinload(School.recovery_policy).selectinload(
                SchoolRecoveryPolicy.required_sensors
            ),
            selectinload(Incident.current_assignee),
            selectinload(Incident.affected_sensors).selectinload(IncidentSensor.sensor),
        )
    )
    if inc is None:
        raise NotFoundError("Incident tidak ditemukan.")

    ensure_region_in_scope(user, inc.school.region_id)

    mon = MonitoringService()
    sch_detail = mon.get_school_detail(session, inc.school_id)
    base = _serialize_incident_summary(inc, sch_detail)

    assignments = session.scalars(
        select(IncidentAssignment)
        .where(IncidentAssignment.incident_id == inc.id)
        .options(selectinload(IncidentAssignment.technician))
        .order_by(IncidentAssignment.id.asc())
    ).all()

    responses = session.scalars(
        select(ResponseLog)
        .where(ResponseLog.incident_id == inc.id)
        .options(selectinload(ResponseLog.actor))
        .order_by(ResponseLog.id.asc())
    ).all()

    verifications = session.scalars(
        select(VerificationRecord)
        .where(VerificationRecord.incident_id == inc.id)
        .options(selectinload(VerificationRecord.supervisor))
        .order_by(VerificationRecord.id.asc())
    ).all()

    rechecks = session.scalars(
        select(PrtgRecheckLog)
        .where(PrtgRecheckLog.incident_id == inc.id)
        .order_by(PrtgRecheckLog.id.asc())
    ).all()

    policy = inc.school.recovery_policy
    base["affected_sensors"] = [
        {
            "sensor_id": link.prtg_sensor_id,
            "prtg_sensor_id": link.sensor.prtg_sensor_id if link.sensor else None,
            "sensor_name": link.sensor.sensor_name if link.sensor else None,
            "raw_status": link.sensor.raw_status if link.sensor else None,
            "normalized_status": link.sensor.normalized_status if link.sensor else None,
            "first_detected_at": _iso_utc(link.first_detected_at),
            "last_seen_down_at": _iso_utc(link.last_seen_down_at),
        }
        for link in inc.affected_sensors
    ]
    base["assignments"] = [
        {
            "id": a.id,
            "technician_id": a.technician_id,
            "technician_name": a.technician.full_name if a.technician else None,
            "assigned_by_id": a.assigned_by_id,
            "assignment_type": a.assignment_type,
            "reason": a.reason,
            "is_active": a.is_active,
            "assigned_at": _iso_utc(a.assigned_at),
            "ended_at": _iso_utc(a.ended_at),
        }
        for a in assignments
    ]
    base["responses"] = [
        {
            "id": r.id,
            "actor_id": r.actor_id,
            "actor_name": r.actor.full_name if r.actor else None,
            "action_summary": r.action_summary,
            "result_notes": r.result_notes,
            "has_attachment": bool(r.attachment_path),
            "attachment_url": f"/api/v1/attachments/{r.id}" if r.attachment_path else None,
            "attachment_mime": r.attachment_mime,
            "attachment_size": r.attachment_size,
            "client_operation_id": r.client_operation_id,
            "created_at": _iso_utc(r.created_at),
        }
        for r in responses
    ]
    base["verifications"] = [
        {
            "id": v.id,
            "supervisor_id": v.supervisor_id,
            "supervisor_name": v.supervisor.full_name if v.supervisor else None,
            "decision": v.decision,
            "reason": v.reason,
            "policy_version": v.policy_version,
            "created_at": _iso_utc(v.created_at),
        }
        for v in verifications
    ]
    base["recheck_logs"] = [
        {
            "id": rc.id,
            "check_sequence": rc.check_sequence,
            "all_required_normal": rc.all_required_normal,
            "is_valid_response": rc.is_valid_response,
            "streak_after_check": rc.streak_after_check,
            "error_message": rc.error_message,
            "snapshot": rc.snapshot_json,
            "checked_at": _iso_utc(rc.checked_at),
        }
        for rc in rechecks
    ]
    base["recovery_policy"] = (
        {
            "configured": len(policy.required_sensors) > 0,
            "version": policy.version,
            "required_consecutive_normal": policy.required_consecutive_normal,
            "recheck_interval_seconds": policy.recheck_interval_seconds,
            "required_sensor_count": len(policy.required_sensors),
        }
        if policy is not None
        else {"configured": False, "version": 0, "required_consecutive_normal": 3, "recheck_interval_seconds": 60, "required_sensor_count": 0}
    )
    return base


def get_incident_history(session: Session, user: User, incident_id: int) -> list[dict]:
    inc = session.scalar(
        select(Incident)
        .where(Incident.id == incident_id)
        .options(selectinload(Incident.school))
    )
    if inc is None:
        raise NotFoundError("Incident tidak ditemukan.")
    ensure_region_in_scope(user, inc.school.region_id)

    rows = session.scalars(
        select(IncidentHistory)
        .where(IncidentHistory.incident_id == inc.id)
        .order_by(IncidentHistory.id.asc())
    ).all()
    return [
        {
            "id": h.id,
            "event_type": h.event_type,
            "from_status": h.from_status,
            "to_status": h.to_status,
            "actor_id": h.actor_id,
            "metadata": h.metadata_json,
            "request_id": h.request_id,
            "created_at": _iso_utc(h.created_at),
        }
        for h in rows
    ]


def list_school_incidents(
    session: Session,
    user: User,
    school_id: int,
    *,
    page: int = 1,
    per_page: int = 20,
) -> dict:
    school = session.scalar(select(School).where(School.id == school_id))
    if school is None:
        raise NotFoundError("Sekolah tidak ditemukan.")
    ensure_region_in_scope(user, school.region_id)
    return list_incidents(session, user, school_id=school.id, page=page, per_page=per_page)
