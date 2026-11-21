"""Immutable incident timeline history helper (FR-020, NFR-04)."""

from __future__ import annotations

from typing import Any, Optional

from flask import g, has_request_context
from sqlalchemy.orm import Session

from app.modules.incidents.models import IncidentHistory


def record_incident_history(
    session: Session,
    *,
    incident_id: int,
    event_type: str,
    from_status: Optional[str],
    to_status: Optional[str],
    actor_id: Optional[int] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> IncidentHistory:
    req_id = getattr(g, "request_id", None) if has_request_context() else None
    entry = IncidentHistory(
        incident_id=incident_id,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        actor_id=actor_id,
        metadata_json=metadata,
        request_id=req_id,
    )
    session.add(entry)
    return entry
