"""Authentication endpoints: login, refresh, logout, and /me (FR-001, FR-002)."""

from __future__ import annotations

from flask import g, request

from app.api.v1 import api_v1
from app.common.errors import BadRequestError
from app.common.rbac import require_auth
from app.common.responses import success_response
from app.infrastructure.database import db
from app.modules.auth.service import (
    authenticate_user,
    refresh_access_token,
    revoke_refresh_token,
    serialize_user_profile,
)


def _json_body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise BadRequestError("Body JSON wajib berupa objek.")
    return data


@api_v1.route("/auth/login", methods=["POST"])
def auth_login():
    body = _json_body()
    result = authenticate_user(
        db.session,
        username=str(body.get("username") or ""),
        password=str(body.get("password") or ""),
    )
    return success_response(result, status_code=200)


@api_v1.route("/auth/refresh", methods=["POST"])
def auth_refresh():
    body = _json_body()
    result = refresh_access_token(db.session, str(body.get("refresh_token") or ""))
    return success_response(result, status_code=200)


@api_v1.route("/auth/logout", methods=["POST"])
@require_auth
def auth_logout():
    body = request.get_json(silent=True) or {}
    revoked = revoke_refresh_token(
        db.session,
        str(body.get("refresh_token") or ""),
        user_id=g.current_user.id,
    )
    return success_response({"logged_out": True, "refresh_token_revoked": revoked})


@api_v1.route("/me", methods=["GET"])
@require_auth
def auth_me():
    return success_response(serialize_user_profile(g.current_user))
