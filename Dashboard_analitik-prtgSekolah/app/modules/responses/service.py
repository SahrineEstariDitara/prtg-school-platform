"""Technician acknowledge, response logs, protected photo uploads, and idempotent offline sync (FR-012..016)."""

from __future__ import annotations

from datetime import timezone
from pathlib import Path
from typing import Any, Optional
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload
from werkzeug.datastructures import FileStorage

from app.common.errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.common.rbac import ensure_region_in_scope
from app.config import BASE_DIR, settings
from app.infrastructure.database.extensions import utcnow
from app.infrastructure.push import FCMAdapter
from app.modules.auth.models import ROLE_TEKNISI
from app.modules.incidents.history import record_incident_history
from app.modules.incidents.models import (
    INCIDENT_ACKNOWLEDGED,
    INCIDENT_ASSIGNED,
    INCIDENT_IN_PROGRESS,
    INCIDENT_PENDING_VERIFICATION,
    Incident,
)
from app.modules.notifications.service import get_region_supervisor_ids, notify_users
from app.modules.responses.models import ResponseLog, SyncOperation
from app.modules.schools.models import School
from app.modules.users.models import User

_ALLOWED_IMAGE_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
)


def _iso_utc(dt) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _detect_image_type(raw_bytes: bytes) -> tuple[str, str]:
    """Validate image magic header (JPEG, PNG, WebP) so spoofed extensions are rejected."""
    for sig, mime, ext in _ALLOWED_IMAGE_SIGNATURES:
        if raw_bytes.startswith(sig):
            return mime, ext
    if len(raw_bytes) >= 12 and raw_bytes[:4] == b"RIFF" and raw_bytes[8:12] == b"WEBP":
        return "image/webp", ".webp"
    raise ValidationError("Lampiran foto harus berformat gambar valid (JPG, PNG, atau WebP).")


def _upload_root() -> Path:
    configured = Path(settings.upload_dir)
    if configured.is_absolute():
        return configured
    return BASE_DIR / configured


def _save_attachment(incident_id: int, uploaded_file: FileStorage) -> tuple[str, str, int]:
    raw_bytes = uploaded_file.read()
    size = len(raw_bytes)
    if size == 0:
        raise ValidationError("File lampiran kosong.")
    if size > settings.max_attachment_bytes:
        max_mb = round(settings.max_attachment_bytes / (1024 * 1024), 2)
        raise ValidationError(f"Ukuran lampiran melebihi batas maksimum {max_mb} MB.")

    mime, ext = _detect_image_type(raw_bytes)
    rel_path = Path("incidents") / str(incident_id) / f"{uuid.uuid4().hex}{ext}"
    abs_path = _upload_root() / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(raw_bytes)
    return rel_path.as_posix(), mime, size


def _require_assigned_technician(incident: Incident, actor: User) -> None:
    if actor.role is None or actor.role.code != ROLE_TEKNISI:
        raise ForbiddenError("Hanya Teknisi yang ditugaskan yang dapat melakukan aksi ini.")
    if incident.current_assignee_id != actor.id:
        raise ForbiddenError("Anda bukan teknisi yang sedang ditugaskan pada incident ini.")


def _serialize_response(row: ResponseLog) -> dict:
    return {
        "id": row.id,
        "incident_id": row.incident_id,
        "actor_id": row.actor_id,
        "action_summary": row.action_summary,
        "result_notes": row.result_notes,
        "has_attachment": bool(row.attachment_path),
        "attachment_url": f"/api/v1/attachments/{row.id}" if row.attachment_path else None,
        "attachment_mime": row.attachment_mime,
        "attachment_size": row.attachment_size,
        "client_operation_id": row.client_operation_id,
        "created_at": _iso_utc(row.created_at),
    }


def acknowledge_incident(session: Session, *, incident_id: int, actor: User) -> dict:
    """Online technician acknowledgement (assigned -> acknowledged, FR-012)."""
    incident = session.scalar(
        select(Incident).where(Incident.id == incident_id).with_for_update()
    )
    if incident is None:
        raise NotFoundError("Incident tidak ditemukan.")

    _require_assigned_technician(incident, actor)

    if incident.incident_status != INCIDENT_ASSIGNED:
        raise ConflictError(
            f"Incident tidak dapat di-acknowledge pada status '{incident.incident_status}'."
        )

    now = utcnow()
    prev_status = incident.incident_status
    incident.incident_status = INCIDENT_ACKNOWLEDGED
    incident.acknowledged_at = now
    session.flush()

    record_incident_history(
        session,
        incident_id=incident.id,
        event_type="incident.acknowledged",
        from_status=prev_status,
        to_status=INCIDENT_ACKNOWLEDGED,
        actor_id=actor.id,
    )
    session.commit()
    return {
        "incident_id": incident.id,
        "incident_status": incident.incident_status,
        "acknowledged_at": _iso_utc(incident.acknowledged_at),
    }


def create_response_log(
    session: Session,
    *,
    incident_id: int,
    actor: User,
    action_summary: str,
    result_notes: Optional[str] = None,
    uploaded_file: Optional[FileStorage] = None,
    client_operation_id: Optional[str] = None,
    commit: bool = True,
) -> tuple[dict, bool]:
    """Create a response log (with optional protected photo) and return (payload, deduplicated)."""
    clean_op_id = (client_operation_id or "").strip() or None
    if clean_op_id:
        existing = session.scalar(
            select(ResponseLog).where(ResponseLog.client_operation_id == clean_op_id)
        )
        if existing is not None:
            if existing.actor_id != actor.id:
                raise ConflictError("client_operation_id sudah digunakan oleh aktor lain.")
            return _serialize_response(existing), True

    clean_summary = (action_summary or "").strip()
    if not clean_summary:
        raise ValidationError("action_summary wajib diisi.")

    incident = session.scalar(
        select(Incident).where(Incident.id == incident_id).with_for_update()
    )
    if incident is None:
        raise NotFoundError("Incident tidak ditemukan.")

    _require_assigned_technician(incident, actor)

    if incident.incident_status not in (INCIDENT_ACKNOWLEDGED, INCIDENT_IN_PROGRESS):
        raise ConflictError(
            f"Tidak dapat mencatat respons pada status '{incident.incident_status}'. "
            "Pastikan incident sudah di-acknowledge dan belum disubmit/resolved."
        )

    attachment_path = None
    attachment_mime = None
    attachment_size = None
    if uploaded_file is not None and uploaded_file.filename:
        attachment_path, attachment_mime, attachment_size = _save_attachment(
            incident.id, uploaded_file
        )

    prev_status = incident.incident_status
    if incident.incident_status == INCIDENT_ACKNOWLEDGED:
        incident.incident_status = INCIDENT_IN_PROGRESS

    row = ResponseLog(
        incident_id=incident.id,
        actor_id=actor.id,
        action_summary=clean_summary[:255],
        result_notes=(result_notes or "").strip() or None,
        attachment_path=attachment_path,
        attachment_mime=attachment_mime,
        attachment_size=attachment_size,
        client_operation_id=clean_op_id,
    )
    session.add(row)
    session.flush()

    record_incident_history(
        session,
        incident_id=incident.id,
        event_type="incident.response_logged",
        from_status=prev_status,
        to_status=incident.incident_status,
        actor_id=actor.id,
        metadata={
            "response_log_id": row.id,
            "has_attachment": bool(attachment_path),
            "client_operation_id": clean_op_id,
        },
    )
    if commit:
        session.commit()
    return _serialize_response(row), False


def get_attachment_file(session: Session, *, response_id: int, actor: User) -> tuple[Path, str]:
    """Authorize region scope before serving an uploaded evidence photo (NFR-10)."""
    row = session.scalar(
        select(ResponseLog)
        .where(ResponseLog.id == response_id)
        .options(selectinload(ResponseLog.incident).selectinload(Incident.school))
    )
    if row is None or not row.attachment_path:
        raise NotFoundError("Lampiran tidak ditemukan.")

    ensure_region_in_scope(actor, row.incident.school.region_id)

    abs_path = (_upload_root() / row.attachment_path).resolve()
    upload_root_resolved = _upload_root().resolve()
    if not str(abs_path).startswith(str(upload_root_resolved)) or not abs_path.is_file():
        raise NotFoundError("Berkas lampiran tidak ditemukan di server.")

    return abs_path, row.attachment_mime or "application/octet-stream"


def submit_resolution(
    session: Session,
    *,
    incident_id: int,
    actor: User,
    summary_notes: Optional[str] = None,
    push_adapter: Optional[FCMAdapter] = None,
) -> dict:
    """Online technician resolution submission (in_progress -> pending_verification, FR-016)."""
    incident = session.scalar(
        select(Incident)
        .where(Incident.id == incident_id)
        .with_for_update()
    )
    if incident is None:
        raise NotFoundError("Incident tidak ditemukan.")

    _require_assigned_technician(incident, actor)

    if incident.incident_status not in (INCIDENT_ACKNOWLEDGED, INCIDENT_IN_PROGRESS):
        raise ConflictError(
            f"Pengajuan penyelesaian tidak valid pada status '{incident.incident_status}'."
        )

    response_count = session.scalar(
        select(func.count(ResponseLog.id)).where(ResponseLog.incident_id == incident.id)
    ) or 0
    if response_count < 1:
        raise ValidationError(
            "Minimal 1 catatan tindakan (ResponseLog) wajib diisi sebelum mengajukan penyelesaian."
        )

    school = session.scalar(select(School).where(School.id == incident.school_id))
    assert school is not None

    now = utcnow()
    prev_status = incident.incident_status
    incident.incident_status = INCIDENT_PENDING_VERIFICATION
    incident.submitted_at = now
    session.flush()

    record_incident_history(
        session,
        incident_id=incident.id,
        event_type="incident.resolution_submitted",
        from_status=prev_status,
        to_status=INCIDENT_PENDING_VERIFICATION,
        actor_id=actor.id,
        metadata={"summary_notes": (summary_notes or "").strip() or None},
    )
    supervisors = get_region_supervisor_ids(session, school.region_id)
    notify_users(
        session,
        recipient_user_ids=supervisors,
        incident_id=incident.id,
        event_type="incident.pending_verification",
        dedup_key=f"inc:{incident.id}:submit:{int(now.timestamp())}",
        title=f"Menunggu verifikasi: {school.name}",
        body=f"Teknisi {actor.full_name} mengajukan penyelesaian gangguan untuk direview.",
        push_adapter=push_adapter,
    )
    session.commit()
    return {
        "incident_id": incident.id,
        "incident_status": incident.incident_status,
        "submitted_at": _iso_utc(incident.submitted_at),
    }


def process_sync_operations(
    session: Session,
    *,
    actor: User,
    operations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Process idempotent offline draft sync queue (FR-014, FR-015, AC-13, TC-12).

    Offline MVP only permits response log drafts (`response_log.create`).
    Conflicts (e.g., incident closed or reassigned) are returned explicitly without silent overwrite.
    """
    if not isinstance(operations, list) or len(operations) == 0:
        raise ValidationError("operations wajib berupa array minimal 1 item.")
    if len(operations) > 50:
        raise ValidationError("Maksimal 50 operasi per batch sinkronisasi.")

    results: list[dict[str, Any]] = []
    now = utcnow()

    for op in operations:
        if not isinstance(op, dict):
            results.append(
                {
                    "client_operation_id": None,
                    "sync_status": "failed",
                    "error": "Format item operasi tidak valid.",
                }
            )
            continue

        op_id = str(op.get("client_operation_id") or "").strip()
        op_type = str(op.get("operation_type") or "").strip()
        payload = op.get("payload") if isinstance(op.get("payload"), dict) else {}

        if not op_id or len(op_id) < 8:
            results.append(
                {
                    "client_operation_id": op_id or None,
                    "sync_status": "failed",
                    "error": "client_operation_id wajib berupa UUID/ID unik minimal 8 karakter.",
                }
            )
            continue

        # 1. Idempotency check on sync_operations
        existing_sync = session.scalar(
            select(SyncOperation).where(SyncOperation.client_operation_id == op_id)
        )
        if existing_sync is not None:
            if existing_sync.user_id != actor.id:
                results.append(
                    {
                        "client_operation_id": op_id,
                        "sync_status": "conflict",
                        "conflict_reason": "client_operation_id sudah dipakai oleh akun lain.",
                    }
                )
                continue
            results.append(
                {
                    "client_operation_id": op_id,
                    "sync_status": existing_sync.sync_status,
                    "deduplicated": True,
                    "conflict_reason": existing_sync.conflict_reason,
                    "result": existing_sync.response_payload_json,
                }
            )
            continue

        # 2. Enforce offline MVP scope boundary (no offline assignment/approval/closure)
        if op_type != "response_log.create":
            sync_row = SyncOperation(
                client_operation_id=op_id,
                user_id=actor.id,
                operation_type=op_type or "unknown",
                sync_status="failed",
                conflict_reason=f"Operasi '{op_type}' wajib dijalankan secara online (ADR-009).",
                processed_at=now,
            )
            session.add(sync_row)
            session.commit()
            results.append(
                {
                    "client_operation_id": op_id,
                    "sync_status": "failed",
                    "error": sync_row.conflict_reason,
                }
            )
            continue

        raw_inc_id = payload.get("incident_id")
        try:
            incident_id = int(raw_inc_id)
        except (TypeError, ValueError):
            results.append(
                {
                    "client_operation_id": op_id,
                    "sync_status": "failed",
                    "error": "payload.incident_id harus berupa integer.",
                }
            )
            continue

        try:
            resp_payload, deduplicated = create_response_log(
                session,
                incident_id=incident_id,
                actor=actor,
                action_summary=str(payload.get("action_summary") or ""),
                result_notes=payload.get("result_notes"),
                client_operation_id=op_id,
                commit=False,
            )
            sync_row = SyncOperation(
                client_operation_id=op_id,
                user_id=actor.id,
                incident_id=incident_id,
                operation_type=op_type,
                sync_status="synced",
                response_payload_json=resp_payload,
                processed_at=now,
            )
            session.add(sync_row)
            session.commit()
            results.append(
                {
                    "client_operation_id": op_id,
                    "sync_status": "synced",
                    "deduplicated": deduplicated,
                    "result": resp_payload,
                }
            )
        except (ConflictError, ForbiddenError, NotFoundError) as exc:
            session.rollback()
            inc_exists = session.scalar(select(Incident.id).where(Incident.id == incident_id))
            sync_row = SyncOperation(
                client_operation_id=op_id,
                user_id=actor.id,
                incident_id=inc_exists,
                operation_type=op_type,
                sync_status="conflict",
                conflict_reason=exc.message,
                processed_at=now,
            )
            session.add(sync_row)
            session.commit()
            results.append(
                {
                    "client_operation_id": op_id,
                    "sync_status": "conflict",
                    "conflict_reason": exc.message,
                }
            )
        except ValidationError as exc:
            session.rollback()
            results.append(
                {
                    "client_operation_id": op_id,
                    "sync_status": "failed",
                    "error": exc.message,
                }
            )

    return results
