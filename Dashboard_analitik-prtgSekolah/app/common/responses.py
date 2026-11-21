"""Standardized JSON response envelopes for REST API v1 (SAD §6.4, §6.5)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from flask import g, jsonify

from app.common.logging import generate_request_id


def _build_meta(extra_meta: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    req_id = getattr(g, "request_id", None) or generate_request_id()
    meta: dict[str, Any] = {
        "request_id": req_id,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    if extra_meta:
        meta.update(extra_meta)
    return meta


def success_response(
    data: Any,
    *,
    status_code: int = 200,
    extra_meta: Optional[dict[str, Any]] = None,
):
    return jsonify({"success": True, "data": data, "meta": _build_meta(extra_meta)}), status_code


def error_response(
    code: str,
    message: str,
    *,
    status_code: int,
    details: Optional[list[dict[str, Any]]] = None,
):
    return (
        jsonify(
            {
                "success": False,
                "error": {
                    "code": code,
                    "message": message,
                    "details": details if details is not None else [],
                },
                "meta": _build_meta(),
            }
        ),
        status_code,
    )
