"""Auto & manual technician assignment service with PostgreSQL row locking (FR-009, FR-010, ADR-013)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.common.audit import record_audit_log
from app.common.errors import ConflictError, NotFoundError, ValidationError
from app.common.rbac import ensure_region_in_scope, resolve_user_region_ids
from app.infrastructure.database.extensions import utcnow
from app.infrastructure.push import FCMAdapter
from app.modules.assignments.models import IncidentAssignment
from app.modules.auth.models import ROLE_TEKNISI, Role
from app.modules.incidents.history import record_incident_history
from app.modules.incidents.models import (
    INCIDENT_ASSIGNED,
    INCIDENT_RESOLVED,
    INCIDENT_UNASSIGNED,
    Incident,
)
from app.modules.notifications.service import (
    get_region_coordinator_and_supervisor_ids,
    notify_users,
)
from app.modules.regions.models import UserRegion
from app.modules.schools.models import School
from app.modules.users.models import User


def _iso_utc(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _active_workload_map(session: Session, technician_ids: list[int]) -> dict[int, int]:
    if not technician_ids:
        return {}
    rows = session.execute(
        select(Incident.current_assignee_id, func.count(Incident.id))
        .where(
            Incident.current_assignee_id.in_(technician_ids),
            Incident.incident_status != INCIDENT_RESOLVED,
        )
        .group_by(Incident.current_assignee_id)
    ).all()
    return {int(uid): int(cnt) for uid, cnt in rows if uid is not None}


def _select_best_technician(session: Session, region_id: int) -> Optional[User]:
    """Eligibility: region match -> active + is_on_duty -> min active workload -> least recently assigned."""
    candidates = session.scalars(
        select(User)
        .join(Role, User.role_id == Role.id)
        .join(UserRegion, UserRegion.user_id == User.id)
        .where(
            Role.code == ROLE_TEKNISI,
            User.is_active.is_(True),
            User.is_on_duty.is_(True),
            UserRegion.region_id == region_id,
        )
        .order_by(User.id.asc())
    ).all()

    if not candidates:
        return None

    workload = _active_workload_map(session, [c.id for c in candidates])
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)

    def sort_key(tech: User):
        last_dt = tech.last_assigned_at
        if last_dt is not None and last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        # Never assigned (None) gets priority over previously assigned (NULLS FIRST)
        return (
            workload.get(tech.id, 0),
            0 if last_dt is None else 1,
            last_dt or epoch,
            tech.id,
        )

    candidates_sorted = sorted(candidates, key=sort_key)
    return candidates_sorted[0]


def auto_assign_incident(
    session: Session,
    incident_id: int,
    *,
    push_adapter: Optional[FCMAdapter] = None,
    commit: bool = True,
) -> Incident:
    """Assign an incident automatically under PostgreSQL row lock (SELECT FOR UPDATE)."""
    incident = session.scalar(
        select(Incident)
        .where(Incident.id == incident_id)
        .with_for_update()
    )
    if incident is None:
        raise NotFoundError("Incident tidak ditemukan.")

    if incident.incident_status == INCIDENT_RESOLVED:
        return incident

    school = session.scalar(select(School).where(School.id == incident.school_id))
    assert school is not None

    prev_status = incident.incident_status
    now = utcnow()
    chosen = _select_best_technician(session, school.region_id)

    if chosen is None:
        incident.incident_status = INCIDENT_UNASSIGNED
        incident.current_assignee_id = None
        record_incident_history(
            session,
            incident_id=incident.id,
            event_type="incident.unassigned",
            from_status=prev_status,
            to_status=INCIDENT_UNASSIGNED,
            metadata={"reason": "no_eligible_on_duty_technician", "region_id": school.region_id},
        )
        recipients = get_region_coordinator_and_supervisor_ids(session, school.region_id)
        notify_users(
            session,
            recipient_user_ids=recipients,
            incident_id=incident.id,
            event_type="incident.unassigned",
            dedup_key=f"inc:{incident.id}:unassigned",
            title=f"Gangguan belum tertugaskan: {school.name}",
            body="Tidak ada teknisi bertugas di wilayah ini. Mohon lakukan penugasan manual.",
            push_adapter=push_adapter,
        )
    else:
        assignment = IncidentAssignment(
            incident_id=incident.id,
            technician_id=chosen.id,
            assigned_by_id=None,
            assignment_type="auto",
            reason="Penugasan otomatis berdasarkan wilayah, duty, dan beban aktif terendah.",
            is_active=True,
            assigned_at=now,
        )
        session.add(assignment)
        incident.current_assignee_id = chosen.id
        incident.incident_status = INCIDENT_ASSIGNED
        chosen.last_assigned_at = now
        session.flush()

        record_incident_history(
            session,
            incident_id=incident.id,
            event_type="incident.auto_assigned",
            from_status=prev_status,
            to_status=INCIDENT_ASSIGNED,
            metadata={"technician_id": chosen.id, "assignment_id": assignment.id},
        )
        notify_users(
            session,
            recipient_user_ids=[chosen.id],
            incident_id=incident.id,
            event_type="incident.assigned",
            dedup_key=f"inc:{incident.id}:assign:{assignment.id}",
            title=f"Tugas gangguan baru: {school.name}",
            body="Anda ditugaskan menangani gangguan sekolah ini. Buka aplikasi untuk detail.",
            push_adapter=push_adapter,
        )

    if commit:
        session.commit()
    return incident


def manual_assign_incident(
    session: Session,
    *,
    incident_id: int,
    technician_id: int,
    actor: User,
    reason: str,
    push_adapter: Optional[FCMAdapter] = None,
) -> dict:
    """Manual assign or reassign by Koordinator / Supervisor with mandatory reason & history preservation."""
    clean_reason = (reason or "").strip()
    if not clean_reason:
        raise ValidationError("Alasan penugasan/reassignment wajib diisi (FR-010).")

    incident = session.scalar(
        select(Incident)
        .where(Incident.id == incident_id)
        .with_for_update()
    )
    if incident is None:
        raise NotFoundError("Incident tidak ditemukan.")

    school = session.scalar(select(School).where(School.id == incident.school_id))
    assert school is not None
    ensure_region_in_scope(actor, school.region_id)

    if incident.incident_status == INCIDENT_RESOLVED:
        raise ConflictError("Incident yang sudah resolved tidak dapat di-assign ulang.")

    technician = session.scalar(
        select(User)
        .where(User.id == technician_id)
        .options(selectinload(User.user_regions))
    )
    if technician is None or not technician.is_active:
        raise ValidationError("Teknisi target tidak ditemukan atau akun tidak aktif.")
    if technician.role is None or technician.role.code != ROLE_TEKNISI:
        raise ValidationError("Target penugasan harus memiliki role 'teknisi'.")

    tech_region_ids = {ur.region_id for ur in (technician.user_regions or [])}
    if school.region_id not in tech_region_ids:
        raise ValidationError("Teknisi target tidak mencakup wilayah sekolah incident ini.")

    now = utcnow()
    prev_status = incident.incident_status
    prev_assignee_id = incident.current_assignee_id

    # Deactivate previous active assignment(s) without ever deleting history
    active_assignments = session.scalars(
        select(IncidentAssignment).where(
            IncidentAssignment.incident_id == incident.id,
            IncidentAssignment.is_active.is_(True),
        )
    ).all()
    for prev_a in active_assignments:
        prev_a.is_active = False
        prev_a.ended_at = now

    assignment_type = "reassign" if active_assignments or prev_assignee_id is not None else "manual"
    new_assignment = IncidentAssignment(
        incident_id=incident.id,
        technician_id=technician.id,
        assigned_by_id=actor.id,
        assignment_type=assignment_type,
        reason=clean_reason,
        is_active=True,
        assigned_at=now,
    )
    session.add(new_assignment)

    incident.current_assignee_id = technician.id
    incident.incident_status = INCIDENT_ASSIGNED
    technician.last_assigned_at = now
    session.flush()

    record_incident_history(
        session,
        incident_id=incident.id,
        event_type=f"incident.{assignment_type}",
        from_status=prev_status,
        to_status=INCIDENT_ASSIGNED,
        actor_id=actor.id,
        metadata={
            "previous_technician_id": prev_assignee_id,
            "technician_id": technician.id,
            "assignment_id": new_assignment.id,
            "reason": clean_reason,
        },
    )
    record_audit_log(
        session,
        actor_id=actor.id,
        action=f"assignment.{assignment_type}",
        entity_type="incident",
        entity_id=incident.id,
        before={"current_assignee_id": prev_assignee_id, "incident_status": prev_status},
        after={"current_assignee_id": technician.id, "incident_status": INCIDENT_ASSIGNED, "reason": clean_reason},
    )
    notify_users(
        session,
        recipient_user_ids=[technician.id],
        incident_id=incident.id,
        event_type=f"incident.{assignment_type}",
        dedup_key=f"inc:{incident.id}:assign:{new_assignment.id}",
        title=f"Penugasan gangguan: {school.name}",
        body=f"Anda ditugaskan menangani gangguan ini. Alasan: {clean_reason[:120]}",
        push_adapter=push_adapter,
    )
    session.commit()

    return {
        "assignment_id": new_assignment.id,
        "incident_id": incident.id,
        "incident_status": incident.incident_status,
        "assignment_type": new_assignment.assignment_type,
        "previous_technician_id": prev_assignee_id,
        "technician": {
            "id": technician.id,
            "username": technician.username,
            "full_name": technician.full_name,
        },
        "assigned_by_id": actor.id,
        "reason": new_assignment.reason,
        "assigned_at": _iso_utc(new_assignment.assigned_at),
    }


def reassign_by_assignment_id(
    session: Session,
    *,
    assignment_id: int,
    technician_id: int,
    actor: User,
    reason: str,
    push_adapter: Optional[FCMAdapter] = None,
) -> dict:
    existing = session.scalar(
        select(IncidentAssignment).where(IncidentAssignment.id == assignment_id)
    )
    if existing is None:
        raise NotFoundError("Assignment tidak ditemukan.")
    return manual_assign_incident(
        session,
        incident_id=existing.incident_id,
        technician_id=technician_id,
        actor=actor,
        reason=reason,
        push_adapter=push_adapter,
    )


def list_technicians_workload(
    session: Session,
    actor: User,
    *,
    region_id: Optional[int] = None,
) -> list[dict]:
    """Return active technicians in actor's scope along with duty flag & active workload."""
    allowed = resolve_user_region_ids(actor)
    if region_id is not None:
        ensure_region_in_scope(actor, region_id)
        target_regions = [region_id]
    else:
        target_regions = allowed

    stmt = (
        select(User)
        .join(Role, User.role_id == Role.id)
        .where(Role.code == ROLE_TEKNISI, User.is_active.is_(True))
        .options(selectinload(User.user_regions))
        .order_by(User.full_name.asc())
    )
    techs = session.scalars(stmt).all()

    filtered = []
    for t in techs:
        t_rids = {ur.region_id for ur in (t.user_regions or [])}
        if target_regions is not None and not (t_rids & set(target_regions)):
            continue
        filtered.append(t)

    workload = _active_workload_map(session, [t.id for t in filtered])
    return [
        {
            "id": t.id,
            "username": t.username,
            "full_name": t.full_name,
            "is_on_duty": t.is_on_duty,
            "active_workload": workload.get(t.id, 0),
            "last_assigned_at": _iso_utc(t.last_assigned_at),
            "regions": [
                {"id": ur.region.id, "code": ur.region.code, "name": ur.region.name}
                for ur in (t.user_regions or [])
                if ur.region is not None
            ],
        }
        for t in filtered
    ]
