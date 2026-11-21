"""Notification routing, deduplication, device token registration, and inbox (FR-011, FR-025)."""

from __future__ import annotations

from datetime import timezone
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.errors import ValidationError
from app.infrastructure.database.extensions import utcnow
from app.infrastructure.push import FCMAdapter
from app.modules.auth.models import (
    ROLE_KOORDINATOR_TEKNISI,
    ROLE_SUPERVISOR_UTAMA,
    ROLE_SUPERVISOR_WILAYAH,
    Role,
)
from app.modules.notifications.models import DeviceToken, NotificationLog
from app.modules.regions.models import UserRegion
from app.modules.users.models import User


def _iso_utc(dt) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def register_device_token(
    session: Session,
    *,
    user_id: int,
    platform: str,
    token: str,
) -> dict:
    clean_platform = (platform or "").strip().lower()
    clean_token = (token or "").strip()
    if clean_platform not in ("android", "ios"):
        raise ValidationError("platform harus 'android' atau 'ios'.")
    if not clean_token or len(clean_token) < 8:
        raise ValidationError("token perangkat wajib diisi minimal 8 karakter.")

    existing = session.scalar(select(DeviceToken).where(DeviceToken.token == clean_token))
    now = utcnow()
    if existing is None:
        row = DeviceToken(
            user_id=user_id,
            platform=clean_platform,
            token=clean_token,
            is_active=True,
            last_seen_at=now,
        )
        session.add(row)
    else:
        existing.user_id = user_id
        existing.platform = clean_platform
        existing.is_active = True
        existing.last_seen_at = now
        row = existing

    session.commit()
    return {
        "id": row.id,
        "user_id": row.user_id,
        "platform": row.platform,
        "is_active": row.is_active,
        "last_seen_at": _iso_utc(row.last_seen_at),
    }


def list_user_notifications(
    session: Session,
    user_id: int,
    *,
    page: int = 1,
    per_page: int = 20,
) -> dict:
    stmt = (
        select(NotificationLog)
        .where(NotificationLog.recipient_user_id == user_id)
        .order_by(NotificationLog.id.desc())
    )
    rows = session.scalars(stmt).all()
    total = len(rows)
    page_safe = max(int(page), 1)
    per_page_safe = max(min(int(per_page), 100), 1)
    start = (page_safe - 1) * per_page_safe
    items = [
        {
            "id": r.id,
            "incident_id": r.incident_id,
            "event_type": r.event_type,
            "dedup_key": r.dedup_key,
            "provider": r.provider,
            "provider_status": r.provider_status,
            "provider_message_id": r.provider_message_id,
            "created_at": _iso_utc(r.created_at),
        }
        for r in rows[start : start + per_page_safe]
    ]
    return {
        "items": items,
        "pagination": {"page": page_safe, "per_page": per_page_safe, "total": total},
    }


def get_region_supervisor_ids(session: Session, region_id: int) -> list[int]:
    """Return active Supervisor Wilayah (for region_id) + active Supervisor Utama user IDs."""
    sup_utama_ids = session.scalars(
        select(User.id)
        .join(Role, User.role_id == Role.id)
        .where(User.is_active.is_(True), Role.code == ROLE_SUPERVISOR_UTAMA)
    ).all()
    sup_wil_ids = session.scalars(
        select(User.id)
        .join(Role, User.role_id == Role.id)
        .join(UserRegion, UserRegion.user_id == User.id)
        .where(
            User.is_active.is_(True),
            Role.code == ROLE_SUPERVISOR_WILAYAH,
            UserRegion.region_id == region_id,
        )
    ).all()
    return sorted(set(sup_utama_ids) | set(sup_wil_ids))


def get_region_coordinator_and_supervisor_ids(session: Session, region_id: int) -> list[int]:
    """Return active Koordinator Teknisi (for region_id) + regional/global Supervisors."""
    koor_ids = session.scalars(
        select(User.id)
        .join(Role, User.role_id == Role.id)
        .join(UserRegion, UserRegion.user_id == User.id)
        .where(
            User.is_active.is_(True),
            Role.code == ROLE_KOORDINATOR_TEKNISI,
            UserRegion.region_id == region_id,
        )
    ).all()
    sup_ids = get_region_supervisor_ids(session, region_id)
    return sorted(set(koor_ids) | set(sup_ids))


def notify_users(
    session: Session,
    *,
    recipient_user_ids: Iterable[int],
    incident_id: Optional[int],
    event_type: str,
    dedup_key: str,
    title: str,
    body: str,
    push_adapter: Optional[FCMAdapter] = None,
) -> list[NotificationLog]:
    """Route deduplicated minimal push notifications and persist delivery logs (FR-011, TC-13)."""
    adapter = push_adapter or FCMAdapter()
    created_logs: list[NotificationLog] = []
    unique_recipients = sorted({int(uid) for uid in recipient_user_ids if uid is not None})

    for uid in unique_recipients:
        already_sent = session.scalar(
            select(NotificationLog.id).where(
                NotificationLog.recipient_user_id == uid,
                NotificationLog.dedup_key == dedup_key,
            )
        )
        if already_sent is not None:
            continue

        tokens = session.scalars(
            select(DeviceToken).where(
                DeviceToken.user_id == uid,
                DeviceToken.is_active.is_(True),
            )
        ).all()

        if not tokens:
            log = NotificationLog(
                recipient_user_id=uid,
                incident_id=incident_id,
                event_type=event_type,
                dedup_key=dedup_key,
                provider="fcm",
                provider_status="no_token",
            )
            session.add(log)
            created_logs.append(log)
            continue

        payload_data = {
            "event_type": event_type,
            "incident_id": str(incident_id or ""),
            "deep_link": f"prtg-school://incidents/{incident_id}" if incident_id else "prtg-school://dashboard",
        }
        for tok in tokens:
            res = adapter.send_push(
                token=tok.token,
                platform=tok.platform,
                title=title,
                body=body,
                data=payload_data,
            )
            log = NotificationLog(
                recipient_user_id=uid,
                incident_id=incident_id,
                event_type=event_type,
                dedup_key=dedup_key,
                provider=res.provider,
                provider_status=res.status,
                provider_message_id=res.message_id,
                error_message=res.error_message,
            )
            session.add(log)
            created_logs.append(log)

    return created_logs
