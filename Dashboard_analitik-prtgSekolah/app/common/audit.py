"""Administrative audit trail helper (FR-026, NFR-04, AC-14)."""

from __future__ import annotations

from typing import Any, Optional

from flask import g, has_request_context, request
from sqlalchemy.orm import Session

from app.infrastructure.database.audit_models import AuditLog


def record_audit_log(
    session: Session,
    *,
    actor_id: Optional[int],
    action: str,
    entity_type: str,
    entity_id: object,
    before: Optional[dict[str, Any]] = None,
    after: Optional[dict[str, Any]] = None,
) -> AuditLog:
    """Append an immutable administrative audit entry."""
    req_id = getattr(g, "request_id", None) if has_request_context() else None
    ip_addr = request.remote_addr if has_request_context() else None

    entry = AuditLog(
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        before_json=before,
        after_json=after,
        request_id=req_id,
        ip_address=ip_addr,
    )
    session.add(entry)
    return entry
