"""Supervisor verification (approve/reject) & multi-check PRTG recovery recheck (FR-017..019, ADR-009..010)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.common.audit import record_audit_log
from app.common.errors import ConflictError, NotFoundError, ValidationError
from app.common.rbac import ensure_region_in_scope
from app.infrastructure.database.extensions import utcnow
from app.infrastructure.push import FCMAdapter
from app.integrations.prtg import PRTGAdapter
from app.modules.incidents.history import record_incident_history
from app.modules.incidents.models import (
    INCIDENT_AWAITING_RECOVERY,
    INCIDENT_IN_PROGRESS,
    INCIDENT_PENDING_VERIFICATION,
    INCIDENT_RESOLVED,
    Incident,
)
from app.modules.notifications.service import get_region_supervisor_ids, notify_users
from app.modules.schools.models import (
    NETWORK_STATUS_ONLINE,
    NETWORK_STATUS_WARNING,
    School,
    SchoolRecoveryPolicy,
    SchoolRecoveryPolicySensor,
)
from app.modules.users.models import User
from app.modules.verification.models import PrtgRecheckLog, VerificationRecord


def _iso_utc(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _sensor_meets_expected(normalized_status: str, expected_status: str) -> bool:
    if expected_status == NETWORK_STATUS_ONLINE:
        return normalized_status == NETWORK_STATUS_ONLINE
    if expected_status == NETWORK_STATUS_WARNING:
        return normalized_status in (NETWORK_STATUS_ONLINE, NETWORK_STATUS_WARNING)
    return False


def reject_resolution(
    session: Session,
    *,
    incident_id: int,
    supervisor: User,
    reason: str,
    push_adapter: Optional[FCMAdapter] = None,
) -> dict:
    """Supervisor rejects technician submission -> returns to `in_progress` + saves VerificationRecord (ADR-008)."""
    clean_reason = (reason or "").strip()
    if not clean_reason:
        raise ValidationError("Alasan penolakan (reason) wajib diisi saat reject.")

    incident = session.scalar(
        select(Incident).where(Incident.id == incident_id).with_for_update()
    )
    if incident is None:
        raise NotFoundError("Incident tidak ditemukan.")

    school = session.scalar(select(School).where(School.id == incident.school_id))
    assert school is not None
    ensure_region_in_scope(supervisor, school.region_id)

    if incident.incident_status != INCIDENT_PENDING_VERIFICATION:
        raise ConflictError(
            f"Verifikasi reject hanya berlaku pada status '{INCIDENT_PENDING_VERIFICATION}', "
            f"bukan '{incident.incident_status}'."
        )

    prev_status = incident.incident_status
    policy = session.scalar(
        select(SchoolRecoveryPolicy).where(SchoolRecoveryPolicy.school_id == school.id)
    )
    ver = VerificationRecord(
        incident_id=incident.id,
        supervisor_id=supervisor.id,
        decision="rejected",
        reason=clean_reason,
        policy_version=policy.version if policy is not None else None,
    )
    session.add(ver)

    incident.incident_status = INCIDENT_IN_PROGRESS
    incident.recovery_stable_streak = 0
    session.flush()

    record_incident_history(
        session,
        incident_id=incident.id,
        event_type="incident.verification_rejected",
        from_status=prev_status,
        to_status=INCIDENT_IN_PROGRESS,
        actor_id=supervisor.id,
        metadata={"verification_record_id": ver.id, "reason": clean_reason},
    )
    record_audit_log(
        session,
        actor_id=supervisor.id,
        action="verification.reject",
        entity_type="incident",
        entity_id=incident.id,
        before={"incident_status": prev_status},
        after={"incident_status": INCIDENT_IN_PROGRESS, "reason": clean_reason},
    )
    if incident.current_assignee_id:
        notify_users(
            session,
            recipient_user_ids=[incident.current_assignee_id],
            incident_id=incident.id,
            event_type="incident.verification_rejected",
            dedup_key=f"inc:{incident.id}:ver:{ver.id}",
            title=f"Laporan ditolak: {school.name}",
            body=f"Perlu tindak lanjut ulang. Alasan: {clean_reason[:120]}",
            push_adapter=push_adapter,
        )

    session.commit()
    return {
        "incident_id": incident.id,
        "incident_status": incident.incident_status,
        "verification": {
            "id": ver.id,
            "decision": ver.decision,
            "reason": ver.reason,
            "supervisor_id": ver.supervisor_id,
            "created_at": _iso_utc(ver.created_at),
        },
    }


def evaluate_recovery_recheck(
    session: Session,
    incident: Incident,
    *,
    prtg_adapter: Optional[PRTGAdapter] = None,
    verification_record_id: Optional[int] = None,
    push_adapter: Optional[FCMAdapter] = None,
    commit: bool = True,
) -> dict:
    """Perform 1 live PRTG recheck of all required school sensors and advance stability streak (ADR-009, ADR-010)."""
    school = session.scalar(
        select(School)
        .where(School.id == incident.school_id)
        .options(
            selectinload(School.recovery_policy)
            .selectinload(SchoolRecoveryPolicy.required_sensors)
            .selectinload(SchoolRecoveryPolicySensor.sensor)
        )
    )
    assert school is not None

    next_seq = (
        session.scalar(
            select(func.coalesce(func.max(PrtgRecheckLog.check_sequence), 0)).where(
                PrtgRecheckLog.incident_id == incident.id
            )
        )
        or 0
    ) + 1

    policy = school.recovery_policy
    required_items = list(policy.required_sensors) if policy is not None else []

    # Guard 1: Missing recovery policy MUST block closure (SAD §8.4, AC-11)
    if policy is None or len(required_items) == 0:
        incident.recovery_stable_streak = 0
        recheck_log = PrtgRecheckLog(
            incident_id=incident.id,
            verification_record_id=verification_record_id,
            check_sequence=next_seq,
            all_required_normal=False,
            is_valid_response=False,
            streak_after_check=0,
            snapshot_json={"policy_configured": False},
            error_message="RECOVERY_POLICY_NOT_CONFIGURED: Sensor wajib sekolah belum diatur oleh Admin.",
        )
        session.add(recheck_log)
        session.flush()
        record_incident_history(
            session,
            incident_id=incident.id,
            event_type="incident.recheck_blocked_missing_policy",
            from_status=incident.incident_status,
            to_status=incident.incident_status,
            metadata={"recheck_log_id": recheck_log.id},
        )
        if commit:
            session.commit()
        return {
            "incident_id": incident.id,
            "incident_status": incident.incident_status,
            "resolved": False,
            "streak": 0,
            "required_consecutive_normal": 3,
            "recheck": {
                "id": recheck_log.id,
                "check_sequence": recheck_log.check_sequence,
                "is_valid_response": False,
                "all_required_normal": False,
                "error_message": recheck_log.error_message,
                "checked_at": _iso_utc(recheck_log.checked_at),
            },
        }

    # Guard 2: Live PRTG recheck of all required sensors
    adapter = prtg_adapter or PRTGAdapter()
    expected_by_prtg_id = {
        rs.sensor.prtg_sensor_id: (rs.sensor, rs.expected_status)
        for rs in required_items
        if rs.sensor is not None
    }
    target_prtg_ids = sorted(expected_by_prtg_id.keys())
    recheck_res = adapter.recheck_sensors(target_prtg_ids)

    now = recheck_res.checked_at
    snapshot_sensors = []
    all_normal = False
    error_message = recheck_res.error_message

    if recheck_res.is_valid:
        all_normal = len(recheck_res.missing_sensor_ids) == 0 and len(target_prtg_ids) > 0
        for prtg_sid in target_prtg_ids:
            sensor_model, expected_status = expected_by_prtg_id[prtg_sid]
            live = recheck_res.sensors_by_id.get(prtg_sid)
            if live is None:
                all_normal = False
                snapshot_sensors.append(
                    {
                        "prtg_sensor_id": prtg_sid,
                        "sensor_name": sensor_model.sensor_name,
                        "expected_status": expected_status,
                        "actual_normalized": "unknown",
                        "raw_status": None,
                        "meets_policy": False,
                    }
                )
                continue

            actual_norm = str(live.get("normalized_status") or "unknown")
            meets = _sensor_meets_expected(actual_norm, expected_status)
            if not meets:
                all_normal = False

            # Update cached sensor row with fresh live telemetry
            sensor_model.raw_status = str(live.get("status") or sensor_model.raw_status)
            sensor_model.raw_status_code = live.get("raw_status_code")
            sensor_model.normalized_status = actual_norm
            sensor_model.last_checked_at = now

            snapshot_sensors.append(
                {
                    "prtg_sensor_id": prtg_sid,
                    "sensor_name": sensor_model.sensor_name,
                    "expected_status": expected_status,
                    "actual_normalized": actual_norm,
                    "raw_status": live.get("status"),
                    "meets_policy": meets,
                }
            )

        if recheck_res.missing_sensor_ids and not error_message:
            error_message = f"Sensor wajib tidak ditemukan di PRTG: {recheck_res.missing_sensor_ids}"
    else:
        all_normal = False

    if recheck_res.is_valid and all_normal:
        incident.recovery_stable_streak = (incident.recovery_stable_streak or 0) + 1
    else:
        incident.recovery_stable_streak = 0

    recheck_log = PrtgRecheckLog(
        incident_id=incident.id,
        verification_record_id=verification_record_id,
        check_sequence=next_seq,
        all_required_normal=all_normal,
        is_valid_response=recheck_res.is_valid,
        streak_after_check=incident.recovery_stable_streak,
        snapshot_json={
            "policy_version": policy.version,
            "required_consecutive_normal": policy.required_consecutive_normal,
            "sensors": snapshot_sensors,
            "missing_sensor_ids": recheck_res.missing_sensor_ids,
        },
        error_message=error_message,
        checked_at=now,
    )
    session.add(recheck_log)
    session.flush()

    resolved_now = False
    if (
        recheck_res.is_valid
        and all_normal
        and incident.recovery_stable_streak >= policy.required_consecutive_normal
    ):
        prev_status = incident.incident_status
        incident.incident_status = INCIDENT_RESOLVED
        incident.resolved_at = now
        resolved_now = True
        record_incident_history(
            session,
            incident_id=incident.id,
            event_type="incident.resolved",
            from_status=prev_status,
            to_status=INCIDENT_RESOLVED,
            metadata={
                "recheck_log_id": recheck_log.id,
                "streak": incident.recovery_stable_streak,
                "required_consecutive_normal": policy.required_consecutive_normal,
                "policy_version": policy.version,
            },
        )
        recipients = get_region_supervisor_ids(session, school.region_id)
        if incident.current_assignee_id:
            recipients.append(incident.current_assignee_id)
        notify_users(
            session,
            recipient_user_ids=recipients,
            incident_id=incident.id,
            event_type="incident.resolved",
            dedup_key=f"inc:{incident.id}:resolved",
            title=f"Gangguan selesai (Resolved): {school.name}",
            body="Seluruh sensor wajib lulus PRTG recheck stabil. Incident resmi ditutup.",
            push_adapter=push_adapter,
        )
    else:
        record_incident_history(
            session,
            incident_id=incident.id,
            event_type="incident.recheck_evaluated",
            from_status=incident.incident_status,
            to_status=incident.incident_status,
            metadata={
                "recheck_log_id": recheck_log.id,
                "is_valid_response": recheck_res.is_valid,
                "all_required_normal": all_normal,
                "streak": incident.recovery_stable_streak,
                "required_consecutive_normal": policy.required_consecutive_normal,
            },
        )

    if commit:
        session.commit()

    return {
        "incident_id": incident.id,
        "incident_status": incident.incident_status,
        "resolved": resolved_now,
        "streak": incident.recovery_stable_streak,
        "required_consecutive_normal": policy.required_consecutive_normal,
        "recheck": {
            "id": recheck_log.id,
            "check_sequence": recheck_log.check_sequence,
            "is_valid_response": recheck_log.is_valid_response,
            "all_required_normal": recheck_log.all_required_normal,
            "streak_after_check": recheck_log.streak_after_check,
            "error_message": recheck_log.error_message,
            "checked_at": _iso_utc(recheck_log.checked_at),
        },
    }


def approve_resolution(
    session: Session,
    *,
    incident_id: int,
    supervisor: User,
    reason: Optional[str] = None,
    prtg_adapter: Optional[PRTGAdapter] = None,
    push_adapter: Optional[FCMAdapter] = None,
) -> dict:
    """Supervisor approves report -> enters `awaiting_recovery` -> executes live PRTG recheck #1."""
    incident = session.scalar(
        select(Incident).where(Incident.id == incident_id).with_for_update()
    )
    if incident is None:
        raise NotFoundError("Incident tidak ditemukan.")

    school = session.scalar(select(School).where(School.id == incident.school_id))
    assert school is not None
    ensure_region_in_scope(supervisor, school.region_id)

    if incident.incident_status != INCIDENT_PENDING_VERIFICATION:
        raise ConflictError(
            f"Verifikasi approve hanya berlaku pada status '{INCIDENT_PENDING_VERIFICATION}', "
            f"bukan '{incident.incident_status}'."
        )

    policy = session.scalar(
        select(SchoolRecoveryPolicy).where(SchoolRecoveryPolicy.school_id == school.id)
    )
    prev_status = incident.incident_status
    ver = VerificationRecord(
        incident_id=incident.id,
        supervisor_id=supervisor.id,
        decision="approved",
        reason=(reason or "").strip() or None,
        policy_version=policy.version if policy is not None else None,
    )
    session.add(ver)

    incident.incident_status = INCIDENT_AWAITING_RECOVERY
    incident.recovery_stable_streak = 0
    session.flush()

    record_incident_history(
        session,
        incident_id=incident.id,
        event_type="incident.verification_approved",
        from_status=prev_status,
        to_status=INCIDENT_AWAITING_RECOVERY,
        actor_id=supervisor.id,
        metadata={"verification_record_id": ver.id, "reason": ver.reason},
    )
    record_audit_log(
        session,
        actor_id=supervisor.id,
        action="verification.approve",
        entity_type="incident",
        entity_id=incident.id,
        before={"incident_status": prev_status},
        after={"incident_status": INCIDENT_AWAITING_RECOVERY, "verification_record_id": ver.id},
    )

    recheck_outcome = evaluate_recovery_recheck(
        session,
        incident,
        prtg_adapter=prtg_adapter,
        verification_record_id=ver.id,
        push_adapter=push_adapter,
        commit=True,
    )
    return {
        **recheck_outcome,
        "verification": {
            "id": ver.id,
            "decision": ver.decision,
            "reason": ver.reason,
            "supervisor_id": ver.supervisor_id,
            "created_at": _iso_utc(ver.created_at),
        },
    }


def trigger_manual_recheck(
    session: Session,
    *,
    incident_id: int,
    supervisor: User,
    prtg_adapter: Optional[PRTGAdapter] = None,
    push_adapter: Optional[FCMAdapter] = None,
) -> dict:
    """Allow authorized supervisor to trigger an immediate recheck for an awaiting_recovery incident."""
    incident = session.scalar(
        select(Incident).where(Incident.id == incident_id).with_for_update()
    )
    if incident is None:
        raise NotFoundError("Incident tidak ditemukan.")

    school = session.scalar(select(School).where(School.id == incident.school_id))
    assert school is not None
    ensure_region_in_scope(supervisor, school.region_id)

    if incident.incident_status != INCIDENT_AWAITING_RECOVERY:
        raise ConflictError(
            f"PRTG recheck manual hanya berlaku pada status '{INCIDENT_AWAITING_RECOVERY}'."
        )

    return evaluate_recovery_recheck(
        session,
        incident,
        prtg_adapter=prtg_adapter,
        push_adapter=push_adapter,
        commit=True,
    )


def run_pending_recovery_rechecks(
    session: Session,
    *,
    prtg_adapter: Optional[PRTGAdapter] = None,
    push_adapter: Optional[FCMAdapter] = None,
    force: bool = False,
) -> list[dict]:
    """Background worker sweep for all `awaiting_recovery` incidents past their check interval."""
    now = utcnow()
    pending_ids = session.scalars(
        select(Incident.id).where(Incident.incident_status == INCIDENT_AWAITING_RECOVERY)
    ).all()

    outcomes: list[dict] = []
    for inc_id in pending_ids:
        inc = session.scalar(
            select(Incident).where(Incident.id == inc_id).with_for_update()
        )
        if inc is None or inc.incident_status != INCIDENT_AWAITING_RECOVERY:
            continue

        policy = session.scalar(
            select(SchoolRecoveryPolicy).where(SchoolRecoveryPolicy.school_id == inc.school_id)
        )
        interval_sec = policy.recheck_interval_seconds if policy is not None else 60

        if not force:
            last_checked = session.scalar(
                select(func.max(PrtgRecheckLog.checked_at)).where(
                    PrtgRecheckLog.incident_id == inc.id
                )
            )
            if last_checked is not None:
                if last_checked.tzinfo is None:
                    last_checked = last_checked.replace(tzinfo=timezone.utc)
                if (now - last_checked).total_seconds() < interval_sec:
                    continue

        outcomes.append(
            evaluate_recovery_recheck(
                session,
                inc,
                prtg_adapter=prtg_adapter,
                push_adapter=push_adapter,
                commit=True,
            )
        )
    return outcomes
