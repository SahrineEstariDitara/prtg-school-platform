"""Offline draft sync, device token registration, and in-app notification inbox endpoints (M5)."""

from __future__ import annotations

from flask import g, request

from app.api.v1 import api_v1
from app.common.errors import BadRequestError
from app.common.rbac import require_auth, require_permission
from app.common.responses import success_response
from app.infrastructure.database import db
from app.modules.notifications.service import list_user_notifications, register_device_token
from app.modules.responses.service import process_sync_operations


def _json_body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise BadRequestError("Body JSON wajib berupa objek.")
    return data


@api_v1.route("/sync/operations", methods=["POST"])
@require_permission("responses.create")
def sync_operations_endpoint():
    body = _json_body()
    results = process_sync_operations(
        db.session,
        actor=g.current_user,
        operations=body.get("operations"),
    )
    return success_response({"results": results})


@api_v1.route("/devices/register", methods=["POST"])
@require_auth
def register_device_endpoint():
    body = _json_body()
    res = register_device_token(
        db.session,
        user_id=g.current_user.id,
        platform=str(body.get("platform") or ""),
        token=str(body.get("token") or ""),
    )
    return success_response(res, status_code=201)


@api_v1.route("/notifications", methods=["GET"])
@require_auth
def list_notifications_endpoint():
    try:
        page = max(int(request.args.get("page", 1)), 1)
        per_page = max(min(int(request.args.get("per_page", 20)), 100), 1)
    except ValueError as exc:
        raise BadRequestError("page dan per_page harus berupa integer.") from exc

    return success_response(
        list_user_notifications(db.session, g.current_user.id, page=page, per_page=per_page)
    )
